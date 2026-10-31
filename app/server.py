"""Small web interface for the reproduction (standard library only).

    python -m app.server                 # http://localhost:8501
    python -m app.server --port 9000 --host 0.0.0.0

Regenerates the paper's numbers, tables and figures from the result files, shows each value of the paper next to
the recomputed one, and keeps a log of every run in out/run_log.jsonl.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from app import catalogue, pipeline

HERE = Path(__file__).resolve().parent
STATE = {"running": False, "step": 0, "steps": 0, "title": "", "started": None, "finished": None, "records": []}
LOCK = threading.Lock()


def _job():
    def progress(i, n, title):
        with LOCK:
            STATE.update(step=i, steps=n, title=title)
    try:
        recs = pipeline.run(progress=progress)
    except Exception as e:  # shown in the interface
        recs = [{"step": "setup", "title": "Working tree", "ok": False, "seconds": 0, "tail": str(e)}]
    with LOCK:
        STATE.update(running=False, finished=time.strftime("%Y-%m-%d %H:%M:%S"), records=recs)


def _figures():
    d = pipeline.OUT / "figures"
    return [{"no": no, "title": t, "file": f, "png": (d / f"{f}.png").exists(), "pdf": (d / f"{f}.pdf").exists()}
            for no, t, f in catalogue.FIGURES]


def _tables():
    d = pipeline.OUT / "manuscript" / "tables"
    return [{"no": no, "title": t, "file": f, "text": (d / f).read_text() if (d / f).exists() else None}
            for no, t, f in catalogue.TABLES]


def _log(n=50):
    if not pipeline.LOG.exists():
        return []
    lines = pipeline.LOG.read_text(encoding="utf-8").splitlines()[-n:]
    return [json.loads(x) for x in reversed(lines)]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/":
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/status":
            with LOCK:
                return self._json(dict(STATE))
        if path == "/api/values":
            have = (pipeline.OUT / "manuscript" / "numbers3.tex").exists()
            return self._json({"generated": have, "values": catalogue.evaluate() if have else []})
        if path == "/api/figures":
            return self._json(_figures())
        if path == "/api/tables":
            return self._json(_tables())
        if path == "/api/log":
            return self._json(_log())
        if path.startswith("/files/"):
            f = (pipeline.OUT / "figures" / path[len("/files/"):]).resolve()
            if f.parent != (pipeline.OUT / "figures").resolve() or not f.exists():
                return self._send(404, "not found", "text/plain")
            return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        if path == "/report.md" and (pipeline.OUT / "report.md").exists():
            return self._send(200, (pipeline.OUT / "report.md").read_bytes(), "text/markdown; charset=utf-8")
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path == "/api/run":
            with LOCK:
                if STATE["running"]:
                    return self._json({"started": False, "reason": "already running"}, 409)
                STATE.update(running=True, step=0, steps=0, title="starting", started=time.strftime("%Y-%m-%d %H:%M:%S"),
                             finished=None, records=[])
            threading.Thread(target=_job, daemon=True).start()
            return self._json({"started": True})
        return self._send(404, "not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8501)
    a = ap.parse_args()
    print(f"Reproduction interface on http://{'localhost' if a.host in ('0.0.0.0', '127.0.0.1') else a.host}:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
