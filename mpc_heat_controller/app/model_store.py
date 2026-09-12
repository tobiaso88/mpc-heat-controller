"""One durable latest successful evaluation, with original CSV and mapping."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

def save(data, content, report):
    data.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(data / 'model.sqlite')) as db:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS evaluation (id INTEGER PRIMARY KEY CHECK(id=1), csv TEXT, report TEXT, saved_at TEXT)')
            db.execute('INSERT OR REPLACE INTO evaluation VALUES (1, ?, ?, ?)',
                       (content, json.dumps(report, allow_nan=False), datetime.now(timezone.utc).isoformat()))

def load(data):
    if not (data / 'model.sqlite').exists(): return None
    with closing(sqlite3.connect(data / 'model.sqlite')) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evaluation'").fetchone(): return None
        row = db.execute('SELECT csv, report, saved_at FROM evaluation WHERE id=1').fetchone()
    return {'csv': row[0], 'report': json.loads(row[1]), 'saved_at': row[2]} if row else None

def save_auto(data, report, status):
    data.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(data / 'model.sqlite')) as db:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS automatic (id INTEGER PRIMARY KEY CHECK(id=1), report TEXT, status TEXT, saved_at TEXT)')
            stamp=datetime.now(timezone.utc).isoformat()
            db.execute('INSERT OR REPLACE INTO automatic VALUES (1, ?, ?, ?)',
                       (json.dumps(report, allow_nan=False), json.dumps(status, allow_nan=False), stamp))

def save_auto_status(data, status):
    data.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(data / 'model.sqlite')) as db:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS automatic (id INTEGER PRIMARY KEY CHECK(id=1), report TEXT, status TEXT, saved_at TEXT)')
            old=db.execute('SELECT report FROM automatic WHERE id=1').fetchone()
            db.execute('INSERT OR REPLACE INTO automatic VALUES (1, ?, ?, ?)',
                       (old[0] if old else None, json.dumps(status, allow_nan=False), datetime.now(timezone.utc).isoformat()))

def load_auto(data):
    if not (data / 'model.sqlite').exists(): return None
    with closing(sqlite3.connect(data / 'model.sqlite')) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automatic'").fetchone(): return None
        row=db.execute('SELECT report,status,saved_at FROM automatic WHERE id=1').fetchone()
    return {'report':json.loads(row[0]) if row[0] else None,
            'status':json.loads(row[1]) if row[1] else None,'saved_at':row[2]} if row else None
