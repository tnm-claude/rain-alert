"""
Tiny liveness state + /health endpoint.

Scheduler jobs call mark("location_check") / mark("radar_fetch") when they succeed;
/health reports the timestamps and returns 503 if the last location check is stale.
"""
from datetime import datetime, timezone

from flask import jsonify

STALE_AFTER_SECONDS = 15 * 60

_started = datetime.now(timezone.utc)
_marks = {}


def mark(name):
    """Record that `name` just succeeded (UTC)."""
    _marks[name] = datetime.now(timezone.utc)


def snapshot():
    now = datetime.now(timezone.utc)
    uptime = (now - _started).total_seconds()
    check = _marks.get("location_check")
    # Before the first check (jobs run every 5 min) allow a grace period of STALE_AFTER.
    age = (now - check).total_seconds() if check else uptime
    ok = age <= STALE_AFTER_SECONDS
    data = {
        "status": "ok" if ok else "stale",
        "uptime_seconds": int(uptime),
        "last_location_check": check.isoformat() if check else None,
        "last_radar_fetch": _marks["radar_fetch"].isoformat() if "radar_fetch" in _marks else None,
        "last_location_check_age_seconds": int(age) if check else None,
    }
    return data, ok


def register(app):
    @app.route("/health")
    def health():
        data, ok = snapshot()
        return jsonify(data), (200 if ok else 503)
