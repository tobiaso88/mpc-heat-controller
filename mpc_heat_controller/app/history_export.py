"""Downloadable observation history and the automatic model's hourly inputs."""
import csv
import io
import json
import logging
import tempfile
import threading
import uuid
import sqlite3
import zipfile
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone

from .auto_model import local_points, mapping_for
from . import model_store


def build(data, config, target=None):
    data = data.resolve()
    # Compress a consistent copy, so a long CSV export does not keep a read
    # transaction open on the live measurement log and block control logging.
    with tempfile.TemporaryDirectory(prefix='mpc-export-snapshot-') as folder:
        snapshot = Path(folder)
        for name in ('measurements.sqlite', 'model.sqlite'):
            source = data / name
            if source.exists():
                with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
                    with closing(sqlite3.connect(snapshot / name)) as copy:
                        original.backup(copy, pages=256)
        return _build(snapshot, config, target)


def _build(data, config, target=None):
    output = io.BytesIO() if target is None else target
    metadata = {'exported_at': datetime.now(timezone.utc).isoformat(),
                'automatic_model': model_store.load_auto(data), 'datasets': {},
                'description': 'Timvärden beräknas från nuvarande mätlogg med samma urval som automatisk träning. Loggen sparas i 90 dagar. Sparade modellresultat kan avse en tidigare version av loggen. Inga luckor fylls.'}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        # Stream rows into ZIP instead of duplicating the full CSV in memory.
        with archive.open('measurements.csv', 'w', force_zip64=True) as member:
            with io.TextIOWrapper(member, encoding='utf-8-sig', newline='') as raw:
                writer = csv.writer(raw)
                writer.writerow(['entity_id', 'state', 'last_changed', 'quality', 'settings'])
                path = data / 'measurements.sqlite'
                if path.exists():
                    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                        for stamp, readings, settings in db.execute('SELECT time, readings, settings FROM samples ORDER BY time'):
                            for reading in json.loads(readings):
                                writer.writerow([reading.get('entity', ''), reading.get('value'), stamp,
                                                 reading.get('quality', ''), settings])
        mappings = {'temperature': mapping_for(config, pv_baseline=bool(config.get('pv_power'))),
                    'cloud': mapping_for(config, auto_cloud=True),
                    'pv': mapping_for(config) if config.get('pv_power') else None}
        seen = []
        for name, mapping in mappings.items():
            if not mapping or mapping in seen:
                continue
            seen.append(mapping)
            points, info = local_points(data, mapping)
            metadata['datasets'][name] = {'mapping': mapping, 'complete_hours': len(points), **info}
            hourly = io.StringIO(newline='')
            writer = csv.writer(hourly)
            extra = next((key for key in ('solar', 'cloud', 'pv_power') if mapping.get(key)), None)
            writer.writerow(['time_utc', 'indoor_mean_c', 'outdoor_c', 'signal_c'] + ([extra] if extra else []))
            for stamp, values in sorted(points.items()):
                writer.writerow([stamp.isoformat(), *values])
            archive.writestr(f'hourly_{name}.csv', hourly.getvalue().encode('utf-8-sig'))
        archive.writestr('model.json', json.dumps(metadata, ensure_ascii=False, indent=2))
    return output.getvalue() if target is None else None


class ExportJob:
    """One bounded export at a time; HTTP requests never wait for ZIP creation."""
    def __init__(self):
        self.lock = threading.Lock()
        self.job = None
        self.directory = None

    def start(self, data, config):
        with self.lock:
            if self.job and self.job['state'] == 'running':
                return dict(self.job)
            if self.directory:
                self.directory.cleanup()
            self.directory = tempfile.TemporaryDirectory(prefix='mpc-export-')
            target = Path(self.directory.name) / 'history.zip'
            self.job = {'id': uuid.uuid4().hex, 'state': 'running'}
            threading.Thread(target=self._run, args=(data, config, target), daemon=True).start()
            return dict(self.job)

    def _run(self, data, config, target):
        try:
            build(data, config, target)
        except Exception:
            logging.exception('Model history export failed')
            with self.lock:
                self.job.update(state='failed', error="Exporten misslyckades. Se appens Logg efter 'Model history export failed'.")
        else:
            with self.lock:
                self.job.update(state='ready')

    def status(self, job_id):
        with self.lock:
            return dict(self.job) if self.job and self.job['id'] == job_id else None

    def open(self, job_id):
        with self.lock:
            if not self.job or self.job['id'] != job_id or self.job['state'] != 'ready':
                return None
            # The open descriptor remains valid if a later export replaces the file.
            return (Path(self.directory.name) / 'history.zip').open('rb')
