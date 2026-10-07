from datetime import datetime, timedelta, timezone

from flask import Flask

from app import health


def _client():
    app = Flask(__name__)
    health.register(app)
    return app.test_client()


def test_health_ok_after_mark(monkeypatch):
    monkeypatch.setattr(health, "_marks", {})
    health.mark("location_check")
    health.mark("radar_fetch")
    resp = _client().get("/health")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert body["last_location_check"] and body["last_radar_fetch"]


def test_health_503_when_check_stale(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(minutes=16)
    monkeypatch.setattr(health, "_marks", {"location_check": old})
    resp = _client().get("/health")
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "stale"


def test_health_grace_before_first_check(monkeypatch):
    monkeypatch.setattr(health, "_marks", {})
    monkeypatch.setattr(health, "_started", datetime.now(timezone.utc) - timedelta(minutes=2))
    assert _client().get("/health").status_code == 200
    monkeypatch.setattr(health, "_started", datetime.now(timezone.utc) - timedelta(minutes=20))
    assert _client().get("/health").status_code == 503
