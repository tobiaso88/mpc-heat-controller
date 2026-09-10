import csv
import io
import json
import math
import os
import tempfile
import threading
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .core import DEFAULT, validate, simulate

DATA = Path(os.environ.get("MPC_DATA", "./data"))
STATIC = Path(__file__).parent / "static"
LOCK = threading.RLock()

def config():
    with LOCK:
        p = DATA / "settings.json"
        return validate(json.loads(p.read_text())) if p.exists() else validate(DEFAULT)

def save(c):
    with LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=DATA)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(validate(c), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, DATA / "settings.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)

def ha_states():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {"connected": False, "entities": [], "message": "Demoläge: Home Assistant är inte ansluten."}
    req = urllib.request.Request("http://supervisor/core/api/states", headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=10) as response:
        states = json.load(response)
    return {"connected": True, "entities": [s for s in states if s["entity_id"].startswith(("sensor.", "weather."))]}

def status(c, source):
    if c["mode"] == "demo":
        return {"mode": "demo", "message": "Simulerat hus och simulerat väder. Modellen är inte kalibrerad.", "temperature": 21.1, "plan": simulate(c)}
    states = {s["entity_id"]: s for s in source["entities"]}
    values, errors = [], []
    for entity in c["indoor"] + [c["outdoor"]]:
        try:
            s = states[entity]
            value = float(s["state"])
            if not math.isfinite(value) or s["attributes"].get("unit_of_measurement") != "°C":
                raise ValueError()
            stamp = datetime.fromisoformat(s.get("last_updated", "").replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
            if not 0 <= age <= 7200:
                raise ValueError()
            if entity in c["indoor"]:
                values.append(value)
        except (KeyError, ValueError, TypeError):
            errors.append(entity)
    return {"mode": "shadow", "temperature": round(sum(values) / len(values), 2) if values and not errors else None,
            "plan": [], "message": "Saknade eller gamla mätvärden: " + ", ".join(errors) if errors else "Mätning fungerar. Styrförslag väntar på validerad husmodell och väderprognos."}

def inspect_csv(content):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not {"entity_id", "state", "last_changed"}.issubset(reader.fieldnames or []):
        raise ValueError("CSV måste ha entity_id, state och last_changed")
    groups = defaultdict(lambda: {"rows": 0, "invalid": 0, "times": [], "values": []})
    for r in reader:
        g = groups[r["entity_id"]]
        g["rows"] += 1
        try:
            t = datetime.fromisoformat(r["last_changed"].replace("Z", "+00:00"))
            if t.tzinfo is None:
                raise ValueError()
            v = float(r["state"])
            if not math.isfinite(v):
                raise ValueError()
            g["times"].append(t)
            g["values"].append(v)
        except (ValueError, TypeError):
            g["invalid"] += 1
    results = []
    for entity, g in groups.items():
        ts = sorted(g.pop("times")); vs = g.pop("values")
        gaps = [(b-a).total_seconds()/3600 for a,b in zip(ts,ts[1:])]
        results.append(dict(entity=entity, **g, start=ts[0].isoformat() if ts else None, end=ts[-1].isoformat() if ts else None,
                            minimum=min(vs) if vs else None, maximum=max(vs) if vs else None,
                            max_gap_hours=max(gaps) if gaps else None))
    return {"entities": results, "message": "Filen är granskad, inte använd för träning. Inga luckor har fyllts."}

class Handler(BaseHTTPRequestHandler):
    def reply(self, code, data, kind="application/json"):
        body = json.dumps(data, ensure_ascii=False).encode() if kind == "application/json" else data
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'self'")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path == "/api/config": return self.reply(200, config())
            if path == "/api/entities": return self.reply(200, ha_states())
            if path == "/api/status":
                c = config()
                return self.reply(200, status(c, ha_states() if c["mode"] == "shadow" else {"entities": []}))
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
            if path in files:
                name, kind = files[path]; return self.reply(200, (STATIC / name).read_bytes(), kind)
            self.reply(404, {"error": "Sidan finns inte"})
        except Exception:
            self.reply(503, {"error": "Kunde inte läsa data. Kontrollera anslutning och konfiguration."})

    def do_POST(self):
        try:
            # JSON/custom-header requests prevent cross-origin form submissions.
            if self.headers.get("X-MPC-Request") != "1":
                return self.reply(403, {"error": "Ogiltig begäran"})
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 20_000_000: return self.reply(413, {"error": "Filgräns 20 MB"})
            body = self.rfile.read(size)
            if self.path == "/api/config":
                c = validate(json.loads(body)); save(c); return self.reply(200, c)
            if self.path == "/api/history": return self.reply(200, inspect_csv(body))
            self.reply(404, {"error": "Åtgärden finns inte"})
        except (ValueError, TypeError, KeyError, UnicodeError) as e:
            self.reply(400, {"error": str(e)})

if __name__ == "__main__":
    ThreadingHTTPServer((os.environ.get("MPC_HOST", "127.0.0.1"), int(os.environ.get("MPC_PORT", "8099"))), Handler).serve_forever()
