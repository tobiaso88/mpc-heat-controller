"""Explicit, user-confirmed transfer of logged outdoor history to a replacement sensor.

Values are not changed: earlier measurements from the old sensor are relabelled as the new
sensor so the automatic model keeps its training period. Both databases are backed up first
and restored if any step fails.
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

BACKUPS_KEPT = 3


def _replace_outdoor(value, old, new):
    """Replace mapping entries {'outdoor': old} at any depth; returns the number replaced."""
    count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'outdoor' and item == old:
                value[key] = new
                count += 1
            else:
                count += _replace_outdoor(item, old, new)
    elif isinstance(value, list):
        for item in value:
            count += _replace_outdoor(item, old, new)
    return count


def _copy(source, target):
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def _backup(data, stamp):
    folder = data / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    made = {}
    for name in ('measurements', 'model'):
        source = data / f'{name}.sqlite'
        if source.exists():
            target = folder / f'{name}-{stamp}.sqlite'
            _copy(source, target)
            made[name] = target
    for name in ('measurements', 'model'):
        for old in sorted(folder.glob(f'{name}-*.sqlite'))[:-BACKUPS_KEPT]:
            old.unlink()
    return made


def _check(rows, old, new):
    for _, raw_readings, raw_settings in rows:
        if json.loads(raw_settings).get('outdoor') != old:
            continue
        for reading in json.loads(raw_readings):
            if reading.get('entity') == new:
                raise ValueError('Den nya utegivaren finns redan i mätloggen under perioden med den gamla. Historiken kan inte flyttas utan att blanda värden.')
            if reading.get('entity') == old and set(reading.get('roles') or ['Utomhus']) != {'Utomhus'}:
                raise ValueError('Den gamla utegivaren användes även i en annan roll. Historiken kan inte flyttas automatiskt.')


def migrate_outdoor(data, old, new):
    if not old or not new or old == new:
        raise ValueError('Välj en ny utegivare som skiljer sig från den nuvarande.')
    if not new.startswith('sensor.'):
        raise ValueError('Den nya utegivaren måste vara en sensor.')
    measurements, model = data / 'measurements.sqlite', data / 'model.sqlite'
    if not measurements.exists():
        raise ValueError('Det finns ingen mätlogg att flytta.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backups = _backup(data, stamp)
    result = {'from': old, 'to': new, 'samples': 0, 'forecasts': 0, 'model': 0,
              'backup': str(backups['measurements'].relative_to(data)),
              'backups': {name: str(path.relative_to(data)) for name, path in backups.items()}}
    try:
        with closing(sqlite3.connect(measurements)) as db:
            with db:
                rows = db.execute('SELECT time, readings, settings FROM samples').fetchall()
                _check(rows, old, new)
                for stamp_row, raw_readings, raw_settings in rows:
                    settings = json.loads(raw_settings)
                    if settings.get('outdoor') != old:
                        continue
                    readings = json.loads(raw_readings)
                    for reading in readings:
                        if reading.get('entity') == old:
                            reading['entity'] = new
                            reading['migrated_from'] = old
                    settings['outdoor'] = new
                    db.execute('UPDATE samples SET readings=?, settings=? WHERE time=?',
                               (json.dumps(readings), json.dumps(settings), stamp_row))
                    result['samples'] += 1
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mpc_forecasts'").fetchone():
                    for stamp_row, raw_mapping in db.execute('SELECT time, mapping FROM mpc_forecasts').fetchall():
                        mapping = json.loads(raw_mapping)
                        if _replace_outdoor(mapping, old, new):
                            db.execute('UPDATE mpc_forecasts SET mapping=? WHERE time=?', (json.dumps(mapping), stamp_row))
                            result['forecasts'] += 1
                db.execute('CREATE TABLE IF NOT EXISTS migrations (time TEXT PRIMARY KEY, kind TEXT NOT NULL, detail TEXT NOT NULL)')
                db.execute('INSERT INTO migrations VALUES (?,?,?)', (datetime.now(timezone.utc).isoformat(), 'outdoor', json.dumps(result)))
        if model.exists():
            with closing(sqlite3.connect(model)) as db:
                with db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automatic'").fetchone():
                        row = db.execute('SELECT report, status FROM automatic WHERE id=1').fetchone()
                        if row:
                            report = json.loads(row[0]) if row[0] else None
                            status = json.loads(row[1]) if row[1] else None
                            result['model'] = _replace_outdoor(report, old, new) + _replace_outdoor(status, old, new)
                            db.execute('UPDATE automatic SET report=?, status=? WHERE id=1',
                                       (json.dumps(report, allow_nan=False) if report is not None else None,
                                        json.dumps(status, allow_nan=False) if status is not None else None))
    except Exception:
        for name, path in backups.items():
            _copy(path, data / f'{name}.sqlite')
        raise
    return result


def restore(data, backups):
    """Undo a migration from its backups; used when saving the new configuration fails."""
    for name in ('measurements', 'model'):
        path = data / backups.get(name, '')
        if backups.get(name) and path.exists():
            _copy(path, data / f'{name}.sqlite')
