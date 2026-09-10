"""One durable latest successful evaluation, with original CSV and mapping."""
import json
import sqlite3
from datetime import datetime, timezone

def save(data, content, report):
    data.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(data / 'model.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS evaluation (id INTEGER PRIMARY KEY CHECK(id=1), csv TEXT, report TEXT, saved_at TEXT)')
        db.execute('INSERT OR REPLACE INTO evaluation VALUES (1, ?, ?, ?)',
                   (content, json.dumps(report, allow_nan=False), datetime.now(timezone.utc).isoformat()))

def load(data):
    if not (data / 'model.sqlite').exists(): return None
    with sqlite3.connect(data / 'model.sqlite') as db:
        row = db.execute('SELECT csv, report, saved_at FROM evaluation WHERE id=1').fetchone()
    return {'csv': row[0], 'report': json.loads(row[1]), 'saved_at': row[2]} if row else None
