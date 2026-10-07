"""Tests for app.ims_radar (IMS radar frame list + image proxy), network mocked."""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app import ims_radar
from app.ims_radar import IMSRadarService


@pytest.fixture(autouse=True)
def _clear_caches():
    ims_radar._frames_cache.update(at=0.0, data=None)
    ims_radar._image_cache.clear()


def _local(dt_utc):
    """UTC datetime -> IMS-style Israel local 'YYYY-MM-DD HH:MM:SS'."""
    return dt_utc.astimezone(ims_radar.ISRAEL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _item(dt_utc, suffix="_0.png", kind="IMSRadar4GIS"):
    name = f"{kind}_{_local(dt_utc).replace('-', '').replace(':', '').replace(' ', '')[:12]}{suffix}"
    return {"forecast_time": _local(dt_utc),
            "file_name": f"/sites/default/files/ims_data/map_images/{kind}/{name}"}


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def test_parse_time_is_israel_local():
    # 13:30 Israel (UTC+3 in October 2026, DST) == 10:30 UTC
    t = IMSRadarService._parse_time("2026-10-07 13:30:00")
    assert datetime.fromtimestamp(t, timezone.utc) == datetime(2026, 10, 7, 10, 30, tzinfo=timezone.utc)


def test_get_frames_filters_and_sorts():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    items = [
        _item(now - timedelta(minutes=5)),
        _item(now - timedelta(minutes=15)),
        _item(now - timedelta(minutes=10), suffix="_1.png"),  # not *_0.png -> skipped
        _item(now + timedelta(hours=1)),                      # future/forecast -> skipped
    ]
    with patch.object(ims_radar.requests, "get", return_value=_resp({"data": {"types": {"IMSRadar": items}}})):
        data = IMSRadarService.get_frames(force=True)
    assert data["source"] == "IMSRadar"
    assert [f["time"] for f in data["frames"]] == sorted(f["time"] for f in data["frames"])
    assert len(data["frames"]) == 2
    assert data["frames"][-1]["url"].startswith("/api/radar/ims/image/IMSRadar4GIS/")
    assert data["bounds"]["west"] < data["bounds"]["east"]
    assert data["bounds"]["south"] < data["bounds"]["north"]


def test_get_frames_falls_back_to_composite_when_main_radar_empty():
    now = datetime.now(timezone.utc)
    item = _item(now - timedelta(minutes=5), suffix=".gif", kind="radarComposite")
    types = {"IMSRadar": [], "radar": [item]}
    with patch.object(ims_radar.requests, "get", return_value=_resp({"data": {"types": types}})):
        data = IMSRadarService.get_frames(force=True)
    assert data["source"] == "radar"
    assert "radarComposite/" in data["frames"][0]["url"]


def test_get_frames_returns_stale_cache_on_failure():
    cached = {"source": "IMSRadar", "frames": [{"time": int(time.time())}], "bounds": {}}
    ims_radar._frames_cache.update(at=0.0, data=cached)  # expired but present
    with patch.object(ims_radar.requests, "get", side_effect=ims_radar.requests.ConnectionError("down")):
        assert IMSRadarService.get_frames() == cached


def test_get_frames_none_when_unreachable_and_no_cache():
    with patch.object(ims_radar.requests, "get", side_effect=ims_radar.requests.ConnectionError("down")):
        assert IMSRadarService.get_frames(force=True) is None


@pytest.mark.parametrize("directory,name", [
    ("evil", "x.png"),
    ("IMSRadar4GIS", "../x.png"),
    ("IMSRadar4GIS", "a/b.png"),
    ("IMSRadar4GIS", "x.php"),
])
def test_get_image_rejects_bad_paths(directory, name):
    with patch.object(ims_radar.requests, "get") as get:
        assert IMSRadarService.get_image(directory, name) is None
        get.assert_not_called()


def test_get_image_fetches_and_caches():
    r = MagicMock(content=b"\x89PNG", headers={"Content-Type": "image/png"})
    r.raise_for_status.return_value = None
    with patch.object(ims_radar.requests, "get", return_value=r) as get:
        assert IMSRadarService.get_image("IMSRadar4GIS", "IMSRadar4GIS_202610071330_0.png") == (b"\x89PNG", "image/png")
        IMSRadarService.get_image("IMSRadar4GIS", "IMSRadar4GIS_202610071330_0.png")
        assert get.call_count == 1


def test_get_image_rejects_non_image_response():
    r = MagicMock(content=b"<html>", headers={"Content-Type": "text/html"})
    r.raise_for_status.return_value = None
    with patch.object(ims_radar.requests, "get", return_value=r):
        assert IMSRadarService.get_image("IMSRadar4GIS", "a_0.png") is None
