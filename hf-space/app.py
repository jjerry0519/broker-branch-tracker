"""
Tiny control plane for the broker-branch-tracker ingest worker on a HF Space.

- GET  /            -> health (also keeps the free Space from pausing)
- GET  /status      -> last run's rc / timing / tail of the log
- POST /run         -> start one full ingest in the background (needs X-Token)
                       body/query: date=YYYY-MM-DD (optional), force=1 (optional)

GitHub Actions fires POST /run once per trading day (~17:45 Asia/Taipei). The
scrape takes ~1-2 h; the endpoint returns immediately and the work runs detached.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
import uvicorn

APP = FastAPI()
RUN_TOKEN = os.environ.get("RUN_TOKEN", "")
STATE = {"running": False, "started_at": None, "finished_at": None, "rc": None}
_PROC: subprocess.Popen | None = None
_LOG = Path("/app/run.out")


def _reap() -> None:
    global _PROC
    if _PROC is not None and _PROC.poll() is not None:
        STATE["running"] = False
        STATE["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        STATE["rc"] = _PROC.returncode
        _PROC = None


@APP.get("/")
def root():
    _reap()
    return {"ok": True, "service": "broker-branch-tracker/ingest", **STATE}


@APP.get("/status")
def status():
    _reap()
    tail = ""
    if _LOG.exists():
        tail = "\n".join(_LOG.read_text(errors="replace").splitlines()[-40:])
    return {**STATE, "log_tail": tail}


@APP.post("/run")
def run(request: Request, x_token: str = Header(default="")):
    global _PROC
    if not RUN_TOKEN or x_token != RUN_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing X-Token")
    _reap()
    if STATE["running"]:
        return {"started": False, "reason": "already running", **STATE}

    args = ["/app/run_once.sh"]
    date = request.query_params.get("date")
    if date:
        args += ["--date", date]
    if request.query_params.get("force"):
        args += ["--force"]

    fh = _LOG.open("wb")
    _PROC = subprocess.Popen(args, stdout=fh, stderr=subprocess.STDOUT, cwd="/app")
    STATE.update(running=True, rc=None, finished_at=None,
                 started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return {"started": True, "pid": _PROC.pid, "args": args}


if __name__ == "__main__":
    uvicorn.run(APP, host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
