"""Web interface (standard library only).

    python -m app.server                 # http://localhost:8501
    python -m app.server --port 9000 --host 0.0.0.0

Simulation: runs the controllers on any load profile, one queued run after another (app/own/). Paper check, on
request: regenerates the paper's numbers, tables and figures from the result files and checks them against the
paper; re-runs single stored experiments when the forecast models are available; keeps every report in out/reports/.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app import figcheck, pipeline, rerun
from app.own import runner as own
from app.reproduce import run_checks, write_report

HERE = Path(__file__).resolve().parent
STATE = {"running": False, "step": 0, "steps": 0, "title": "", "started": None, "finished": None, "records": []}
PREP = {"state": "idle", "message": ""}
LOCK = threading.Lock()


def _job():
    def progress(i, n, title):
        with LOCK:
            STATE.update(step=i, steps=n, title=title)
    try:
        recs = pipeline.run(progress=progress)
        write_report(recs, run_checks())
    except Exception as e:  # shown in the interface
        recs = [{"step": "setup", "title": "Working tree", "ok": False, "seconds": 0, "tail": str(e)}]
    with LOCK:
        STATE.update(running=False, finished=time.strftime("%Y-%m-%d %H:%M:%S"), records=recs)


def _prepare():
    ok, msg = rerun.prepare()
    PREP.update(state="ready" if ok else "error", message=msg)


def _history():
    d = pipeline.OUT / "reports"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("report_*.json"), reverse=True)[:30]:
        r = json.loads(p.read_text())
        out.append({"file": p.name, "time": r.get("time"), "summary": r.get("summary"),
                    "failed_steps": sum(not s["ok"] for s in r.get("steps", []))})
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def log_message(self, *a):  # quiet
        pass

    def _file(self, base: Path, name: str):
        f = (base / name).resolve()
        if f.parent != base.resolve() or not f.exists():
            return self._send(404, "not found", "text/plain")
        return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

    def do_GET(self):
        u = urlparse(self.path); path = unquote(u.path); q = parse_qs(u.query)
        if path == "/":
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/status":
            with LOCK:
                return self._json(dict(STATE))
        if path == "/api/report":
            if not (pipeline.OUT / "manuscript" / "numbers3.tex").exists():
                return self._json({"generated": False})
            return self._json({"generated": True, **run_checks()})
        if path == "/api/history":
            return self._json(_history())
        if path == "/api/log":
            if not pipeline.LOG.exists():
                return self._json([])
            lines = pipeline.LOG.read_text(encoding="utf-8").splitlines()[-60:]
            return self._json([json.loads(x) for x in reversed(lines)])
        if path == "/api/experiments":
            ok, msg = rerun.models_ok(quick=True) if PREP["state"] == "idle" else (PREP["state"] == "ready", PREP["message"])
            return self._json({"models": ok, "message": msg, "prepare": PREP, "experiments": rerun.experiments(),
                               "runs": rerun.all_status()})
        if path == "/api/experiment":
            return self._json(rerun.status(q.get("id", [""])[0]))
        if path == "/api/own/options":
            return self._json(own.options())
        if path == "/api/own/upload_info":
            try:
                return self._json(own.upload_info(q.get("id", [""])[0]))
            except ValueError as e:
                return self._json({"error": str(e)}, 404)
        if path == "/api/own/jobs":
            return self._json(own.jobs())
        if path == "/api/own/job":
            return self._json(own.status(q.get("id", [""])[0]))
        if path == "/api/own/file":
            f = own.file(q.get("id", [""])[0], q.get("name", [""])[0])
            if f:
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "text/plain",
                                  {"Content-Disposition": f'attachment; filename="{q["id"][0]}_{f.name}"'})
        if path == "/files/own/sample.csv":
            g = lambda k, d: q.get(k, [d])[0]  # noqa: E731
            try:
                body, name = own.sample_csv(g("span", "month"), int(g("res", "60")), g("unit", "kw"), float(g("mean", "250")))
            except ValueError:
                return self._send(400, "bad parameters", "text/plain")
            return self._send(200, body, "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}"'})
        if path.startswith("/files/figures/"):
            return self._file(pipeline.OUT / "figures", path[len("/files/figures/"):])
        if path.startswith("/files/reference/"):
            return self._file(figcheck.REF, path[len("/files/reference/"):])
        if path in ("/report.md", "/report.json"):
            f = pipeline.OUT / path[1:]
            if f.exists():
                return self._send(200, f.read_bytes(), "text/markdown; charset=utf-8" if f.suffix == ".md" else "application/json",
                                  {"Content-Disposition": f'attachment; filename="{f.name}"'})
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path); path = u.path; q = parse_qs(u.query)
        if path == "/api/run":
            with LOCK:
                if STATE["running"]:
                    return self._json({"started": False, "reason": "already running"}, 409)
                STATE.update(running=True, step=0, steps=0, title="starting", started=time.strftime("%Y-%m-%d %H:%M:%S"),
                             finished=None, records=[])
            threading.Thread(target=_job, daemon=True).start()
            return self._json({"started": True})
        if path == "/api/experiments/prepare":
            if PREP["state"] != "running":
                PREP.update(state="running", message="checking the models and building the work copies")
                threading.Thread(target=_prepare, daemon=True).start()
            return self._json(PREP)
        if path == "/api/experiments/start":
            if PREP["state"] != "ready":
                ok, msg = rerun.prepare(); PREP.update(state="ready" if ok else "error", message=msg)
                if not ok:
                    return self._json({"started": False, "reason": msg}, 409)
            return self._json(rerun.start(q.get("id", [""])[0]))
        if path == "/api/own/upload":
            n = int(self.headers.get("Content-Length", 0))
            if n > 50 * 1024 * 1024:
                return self._json({"ok": False, "report": {"errors": ["the file is larger than 50 MB"], "warnings": []}}, 413)
            text = self.rfile.read(n).decode("utf-8-sig", errors="replace")
            g = lambda k, d: q.get(k, [d])[0]  # noqa: E731
            return self._json(own.upload(text, g("unit", "kw"), g("tz", "Europe/Budapest"), g("name", "")))
        if path == "/api/own/reference":
            return self._json(own.reference_upload())
        if path in ("/api/own/stop", "/api/own/delete"):
            try:
                fn = own.stop if path.endswith("stop") else own.delete
                return self._json(fn(q.get("id", [""])[0]))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/own/resume":
            try:
                return self._json(own.resume(q.get("id", [""])[0]))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/own/estimate" or path == "/api/own/start":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            try:
                if path.endswith("estimate"):
                    return self._json(own.estimate(body["params"]))
                return self._json(own.start(body["upload_id"], body["params"]))
            except (ValueError, KeyError) as e:
                return self._json({"error": str(e)}, 400)
        return self._send(404, "not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8501)
    a = ap.parse_args()
    print(f"UA-MPC simulation interface on http://{'localhost' if a.host in ('0.0.0.0', '127.0.0.1') else a.host}:{a.port}", flush=True)

    def queue_keeper():   # starts queued runs also when nobody has the page open
        while True:
            try:
                own.dispatch()
            except Exception:
                pass
            time.sleep(15)
    threading.Thread(target=queue_keeper, daemon=True).start()
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
