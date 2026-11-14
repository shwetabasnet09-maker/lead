"""Starts a scan in the background so the page responds straight away."""
from __future__ import annotations

import logging
import subprocess
import sys

from django.conf import settings

from .services import ScanAlreadyRunning, scan_is_running

logger = logging.getLogger(__name__)


def celery_enabled() -> bool:
    return bool(settings.CELERY_BROKER_URL)


def _start_process(source_id: int | None) -> None:
    # Without Redis (for example on cPanel), run the same command that cron runs, detached from this request.
    command = [sys.executable, str(settings.BASE_DIR / "manage.py"), "run_scrapers"]
    if source_id:
        command += ["--source", str(source_id)]
    log_dir = settings.BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log = open(log_dir / "scan.log", "a", encoding="utf-8")  # The child keeps its own copy of this handle.
    options = {"cwd": settings.BASE_DIR, "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        # A hidden console, not DETACHED_PROCESS: the .venv python.exe is a launcher that starts the real
        # Python, and a detached launcher makes Windows open a visible console window for that child.
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen(command, **options)
    finally:
        log.close()


def start_scan(source_id: int | None = None) -> str:
    """Returns how the scan was started: "celery" or "process"."""
    if scan_is_running():
        raise ScanAlreadyRunning("A scan is already running. Wait for it to finish.")
    if celery_enabled():
        from .tasks import run_scan

        try:
            run_scan.delay(source_id)
            return "celery"
        except Exception:
            logger.exception("Could not reach Redis, so the scan is starting as a separate process instead")
    _start_process(source_id)
    return "process"
