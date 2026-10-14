"""Downloadable observation history and the automatic model's hourly inputs."""
import csv
import io
import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone

from .auto_model import local_points, mapping_for
from . import model_store


def build(data, config):
    data = data.resolve()
    output = io.BytesIO()
    metadata = {'exported_at': datetime.now(timezone.utc).isoformat(),
                'automatic_model': model_store.load_auto(data), 'datasets': {},
                'description': 'Timvärden beräknas från nuvarande mätlogg med samma urval som automatisk träning. Loggen sparas i 90 dagar. Sparade modellresultat kan avse en tidigare version av loggen. Inga luckor fylls.'}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        raw = io.StringIO(newline='')
        writer = csv.writer(raw)
        writer.writerow(['entity_id', 'state', 'last_changed', 'quality', 'settings'])
        path = data / 'measurements.sqlite'
        if path.exists():
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                for stamp, readings, settings in db.execute('SELECT time, readings, settings FROM samples ORDER BY time'):
                    for reading in json.loads(readings):
                        writer.writerow([reading.get('entity', ''), reading.get('value'), stamp,
                                         reading.get('quality', ''), settings])
        archive.writestr('measurements.csv', raw.getvalue().encode('utf-8-sig'))
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
    return output.getvalue()
