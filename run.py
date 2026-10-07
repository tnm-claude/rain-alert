#!/usr/bin/env python3
"""
Rain Alert App - Server Entry Point

Run as a LaunchAgent (see scripts/install-service.sh). All output, both print() and
logging, goes to a size-bounded rotating file: logs/rain-alert.log.
"""
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class _Formatter(logging.Formatter):
    """Timestamped records with ANSI colors (added by werkzeug) stripped."""

    _ansi = re.compile(r"\x1b\[[0-9;]*m")

    def format(self, record):
        return self._ansi.sub("", super().format(record))


class _LogStream:
    """File-like object that turns each line written to it into a log record."""

    def __init__(self, logger, level):
        self._logger = logger
        self._level = level
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line.rstrip())
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def setup_logging():
    """Send logging + stdout/stderr to logs/rain-alert.log (2 MB x 6 files by default)."""
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "rain-alert.log"),
        maxBytes=int(os.getenv("LOG_MAX_BYTES", str(2 * 1024 * 1024))),
        backupCount=int(os.getenv("LOG_BACKUP_COUNT", "5")),
        encoding="utf-8",
    )
    handler.setFormatter(_Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr.isatty():  # also echo to the console when run by hand
        root.addHandler(logging.StreamHandler(sys.stderr))
    sys.stdout = _LogStream(logging.getLogger("stdout"), logging.INFO)
    sys.stderr = _LogStream(logging.getLogger("stderr"), logging.ERROR)


setup_logging()

from app import create_app

# Create Flask application
app = create_app()

if __name__ == "__main__":
    # Get host and port from environment or use defaults
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))

    print(f"Rain Alert starting on http://{host}:{port}")

    # use_reloader=False: the reloader would start a second process and a second scheduler
    app.run(host=host, port=port, debug=False, use_reloader=False)
