"""Tests for alert capture: UTC windows, buffers, pruning, detection log. No network."""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
import requests
from flask import Flask
from PIL import Image, ImageDraw

from app import capture
from app.models import Alert, DetectionCheck, Location, db
from app.radar import RadarService, is_valid_tile, parse_stamp, utc_now

IDT = timezone(timedelta(hours=3))


def unix(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def png(mode='RGBA', size=512, blob=True):
    img = Image.new(mode, (size, size))
    if blob and mode == 'RGBA':
        ImageDraw.Draw(img).ellipse([300, 200, 360, 260], fill=(0, 120, 255, 200))
    buf = BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def big_png(seed):
    """A > 10 KB RGB PNG that differs per seed (stands in for the weather2day image)."""
    img = Image.effect_noise((128, 128), 50 + seed).convert('RGB')
    buf = BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def response(content=b'', status=200, url='', json_data=None):
    def raise_for_status():
        if status >= 400:
            raise requests.HTTPError(status)
    return SimpleNamespace(status_code=status, content=content, url=url,
                           json=lambda: json_data, raise_for_status=raise_for_status)


@pytest.fixture
def israel_tz():
    """Run with the server in Israel time (the bug #2 condition)."""
    old = os.environ.get('TZ')
    os.environ['TZ'] = 'Asia/Jerusalem'
    time.tzset()
    yield
    if old is None:
        os.environ.pop('TZ', None)
    else:
        os.environ['TZ'] = old
    time.tzset()


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    radar = data / 'radar'
    radar.mkdir(parents=True)
    monkeypatch.setattr(RadarService, 'get_radar_directory', staticmethod(lambda: str(radar)))
    monkeypatch.setattr(capture, 'DATA_DIR', str(data))
    monkeypatch.setattr(capture, 'ALERTS_DIR', str(data / 'alerts'))
    return SimpleNamespace(data=data, radar=radar, alerts=data / 'alerts')


@pytest.fixture
def flask_app(tmp_path):
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{tmp_path / 'test.db'}"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


def frames_every_10_min(start, count):
    return [{'time': unix(start + timedelta(minutes=10 * i)), 'path': f'/v2/radar/f{i}'}
            for i in range(count)]


# ---------- UTC window selection ----------

def test_frames_in_window_selects_60_utc_minutes(israel_tz):
    frames = frames_every_10_min(datetime(2026, 4, 8, 4, 40), 14)  # 04:40 .. 06:50 UTC
    alert_time = datetime(2026, 4, 8, 6, 54, 10)  # alert 54 from last season, UTC
    selected = capture.frames_in_window(frames, alert_time)
    times = [ts.strftime('%H:%M') for ts, _ in selected]
    assert times == ['06:00', '06:10', '06:20', '06:30', '06:40', '06:50']


def test_frames_in_window_accepts_aware_alert_time(israel_tz):
    frames = frames_every_10_min(datetime(2026, 4, 8, 4, 40), 14)
    naive = capture.frames_in_window(frames, datetime(2026, 4, 8, 6, 54))
    aware = capture.frames_in_window(frames, datetime(2026, 4, 8, 9, 54, tzinfo=IDT))
    assert [ts for ts, _ in naive] == [ts for ts, _ in aware]


def test_buffer_names_are_utc_and_match_alert_window(israel_tz, dirs, monkeypatch):
    """Regression for #2: buffer files were named in local time (UTC+3 in April) so a
    UTC alert window never matched any of them."""
    frames = frames_every_10_min(datetime(2026, 4, 8, 5, 0), 12)  # 05:00 .. 06:50 UTC
    api = {'host': 'https://tiles.test', 'radar': {'past': frames}}

    def fake_get(url, **kwargs):
        return response(json_data=api) if 'weather-maps' in url else response(png())
    monkeypatch.setattr(requests, 'get', fake_get)

    assert RadarService.fetch_all_radar_images() == 12
    assert 'radar_20260408T0650Z.png' in os.listdir(dirs.radar)
    alert_time = datetime(2026, 4, 8, 6, 54, 10)
    found = RadarService.buffered_images(str(dirs.radar), 'radar_', alert_time - capture.WINDOW, alert_time)
    assert [ts.strftime('%H:%M') for ts, _ in found] == ['06:00', '06:10', '06:20', '06:30', '06:40', '06:50']


def test_zoom_placeholder_tile_is_rejected():
    assert is_valid_tile(png('RGBA'))
    assert not is_valid_tile(png('P'))  # 'Zoom Level Not Supported' is a palette PNG served with HTTP 200
    assert not is_valid_tile(b'<html>')


# ---------- rolling buffers ----------

def test_cleanup_bounds_buffer_by_time(dirs):
    now = datetime(2026, 10, 7, 12, 0)
    keep = ['radar_20261007T1010Z.png', 'radar_20261007T1150Z.png']
    drop = ['radar_20261007T0950Z.png', 'radar_202610071150.png']  # too old; legacy local-time name
    for name in keep + drop:
        (dirs.radar / name).write_bytes(b'x')

    assert RadarService.cleanup_old_images(now=now) == 2
    assert sorted(os.listdir(dirs.radar)) == sorted(keep)


def test_weather2day_uses_redirect_epoch_as_utc_time(monkeypatch):
    epoch = unix(datetime(2026, 10, 7, 10, 32, 1))
    content = big_png(1)
    monkeypatch.setattr(requests, 'get', lambda url, **kw: response(
        content, url=f'https://www.weather2day.co.il/pics/radar/{epoch}.png'))
    assert RadarService.fetch_weather2day() == (datetime(2026, 10, 7, 10, 32, 1), content)


def test_weather2day_broken_source_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr(requests, 'get', lambda url, **kw: response(b'<html>gone</html>', status=404, url=url))
    assert RadarService.fetch_weather2day() is None

    def boom(url, **kw):
        raise requests.ConnectionError('down')
    monkeypatch.setattr(requests, 'get', boom)
    assert RadarService.fetch_weather2day() is None


# ---------- IMS ----------

def ims_items(first_local, count, step=5):
    items = []
    for i in range(count):
        t = first_local + timedelta(minutes=step * i)
        name = f"/sites/default/files/ims_data/map_images/IMSRadar4GIS/IMSRadar4GIS_{t:%Y%m%d%H%M}_0.png"
        items.append({'forecast_time': f'{t:%Y-%m-%d %H:%M:%S}', 'file_name': name})
        items.append({'forecast_time': f'{t:%Y-%m-%d %H:%M:%S}', 'file_name': name.replace('_0.png', '_1.png')})
    return items


def test_ims_forecast_time_is_israel_local(israel_tz):
    """IMS lists frames in Israel local time (IDT = UTC+3 in April); the window is UTC."""
    items = ims_items(datetime(2026, 4, 8, 8, 50), 14)  # 08:50 .. 09:55 IDT = 05:50 .. 06:55 UTC
    selected = capture.ims_frames_in_window(items, datetime(2026, 4, 8, 6, 54, 10))
    assert [ts.strftime('%H:%M') for ts, _ in selected][0::11] == ['05:55', '06:50']
    assert len(selected) == 12
    assert selected[0][1].startswith('https://ims.gov.il/sites/') and selected[0][1].endswith('0855_0.png')


def test_ims_winter_time_offset_is_two_hours():
    items = ims_items(datetime(2026, 12, 1, 9, 0), 1)  # IST = UTC+2
    assert capture.ims_frames_in_window(items, datetime(2026, 12, 1, 7, 30))[0][0] == datetime(2026, 12, 1, 7, 0)


def test_render_ims_georeferences_the_location():
    lat, lon = 32.0828, 34.8101
    img = Image.new('RGBA', (940, 940))
    px, py = capture.ims_pixel(lat, lon, 940, 940)
    ImageDraw.Draw(img).ellipse([px - 30, py - 30, px + 30, py + 30], fill=(0, 200, 0, 255))
    buf = BytesIO()
    img.save(buf, 'PNG')
    out = capture.render_ims(buf.getvalue(), lat, lon, datetime(2026, 4, 8, 6, 50))
    c = capture.OUT_PX // 2
    assert out.getpixel((c + 40, c + 40)) == (0, 200, 0)  # blob around the marker
    assert out.getpixel((5, capture.OUT_PX - 5)) != (0, 200, 0)  # but not ~60 km away
    assert capture.ims_pixel(capture.IMS_BOUNDS[3], capture.IMS_BOUNDS[0], 940, 940) == (0, 0)


# ---------- pruning ----------

def make_alert_dirs(root, ids, size=100_000):
    for aid in ids:
        d = root / str(aid)
        d.mkdir(parents=True)
        (d / 'frame.png').write_bytes(b'\0' * size)


def test_prune_deletes_oldest_unlabelled_first_then_labelled(tmp_path):
    make_alert_dirs(tmp_path, [1, 2, 3, 4, 5, 6])  # 600 KB
    removed = capture.prune(max_bytes=350_000, alerts_dir=str(tmp_path), labelled_ids={2, 3})
    assert removed == [1, 4, 5]
    assert sorted(os.listdir(tmp_path)) == ['2', '3', '6']

    removed = capture.prune(max_bytes=150_000, alerts_dir=str(tmp_path), labelled_ids={2, 3})
    assert removed == [6, 2]  # unlabelled gone, then the oldest labelled
    assert capture.dir_size(str(tmp_path)) <= 150_000


def test_prune_under_cap_is_a_noop_and_respects_keep(tmp_path):
    make_alert_dirs(tmp_path, [1, 2])
    assert capture.prune(max_bytes=10**9, alerts_dir=str(tmp_path), labelled_ids=set()) == []
    assert capture.prune(max_bytes=0, alerts_dir=str(tmp_path), labelled_ids=set(), keep={2}) == [1]
    assert os.listdir(tmp_path) == ['2']


def test_prune_reads_labels_from_db(flask_app, tmp_path):
    loc = Location(address='x', latitude=32.08, longitude=34.81)
    db.session.add(loc)
    db.session.commit()
    for aid, feedback in ((1, True), (2, None), (3, False)):
        db.session.add(Alert(id=aid, location_id=loc.id, rain_expected_at=datetime(2026, 1, 1),
                             minutes_ahead=10, message='m', user_feedback=feedback))
    db.session.commit()
    make_alert_dirs(tmp_path / 'alerts', [1, 2, 3, 4])  # 4 has no DB row: treated as unlabelled
    assert capture.prune(max_bytes=150_000, alerts_dir=str(tmp_path / 'alerts')) == [2, 4, 1]


# ---------- detection log ----------

def test_log_check_none_means_no_rain(flask_app):
    row = capture.log_check(SimpleNamespace(id=7), None)
    assert row is not None
    stored = DetectionCheck.query.one()
    assert (stored.location_id, stored.should_alert, stored.reason) == (7, False, 'no rain')
    assert stored.distance_km is None and stored.details is None
    assert abs((utc_now() - stored.checked_at).total_seconds()) < 60


def test_log_check_maps_fields_and_keeps_details_compact(flask_app):
    diagnostics = {
        'should_alert': True, 'reason': 'approaching', 'current_distance_km': 12.3456,
        'max_dbz': 41.5, 'intensity': 160, 'velocity_kmh': 45.2, 'minutes_until_rain': 16,
        'expected_at': datetime(2026, 4, 8, 7, 10), 'direction': 'SW',
        'frames': [{'time': datetime(2026, 4, 8, 6, i), 'distance_km': 30 - i, 'pixels': list(range(500))}
                   for i in range(12)],
    }
    capture.log_check(SimpleNamespace(id=6), diagnostics)
    row = DetectionCheck.query.one()
    assert row.should_alert is True and row.reason == 'approaching'
    assert (row.distance_km, row.max_dbz, row.velocity_kmh, row.eta_minutes) == (12.3456, 41.5, 45.2, 16)
    details = json.loads(row.details)
    assert len(row.details) <= capture.DETAILS_MAX_CHARS
    assert details['expected_at'] == '2026-04-08T07:10:00Z'
    assert details['frames_n'] == 12
    assert 'pixels' not in row.details


def test_log_check_legacy_rain_info_counts_as_alert(flask_app):
    capture.log_check(SimpleNamespace(id=6), {'minutes_until_rain': 0, 'intensity': 200})
    row = DetectionCheck.query.one()
    assert row.should_alert is True and row.reason == 'rain detected' and row.eta_minutes == 0


def test_log_check_never_raises():
    assert capture.log_check(SimpleNamespace(id=1), None) is None  # no app context


def test_prune_checks_drops_rows_older_than_180_days(flask_app):
    now = utc_now()
    for days in (10, 179, 181, 400):
        db.session.add(DetectionCheck(checked_at=now - timedelta(days=days), location_id=1, should_alert=False))
    db.session.commit()
    assert capture.prune_checks() == 2
    assert DetectionCheck.query.count() == 2


# ---------- snapshot ----------

def fake_alert(created_at, alert_id=42):
    loc = SimpleNamespace(id=6, address='Ramat Gan', latitude=32.0828, longitude=34.8101)
    return SimpleNamespace(id=alert_id, location_id=6, location=loc, created_at=created_at,
                           alert_time=created_at, rain_expected_at=created_at + timedelta(minutes=15),
                           minutes_ahead=15, message='rain', dismissed=False, user_feedback=None,
                           radar_images_saved=None)


def test_snapshot_alert_writes_utc_frames_preview_and_json(israel_tz, dirs, monkeypatch):
    created = datetime(2026, 4, 8, 6, 54, 10)
    frames = frames_every_10_min(datetime(2026, 4, 8, 4, 50), 13)  # 04:50 .. 06:50 UTC, like the API
    api = {'host': 'https://tiles.test', 'radar': {'past': frames}}
    ims = {'data': {'types': {'IMSRadar': ims_items(datetime(2026, 4, 8, 8, 0), 24)}}}  # 08:00 .. 09:55 IDT
    urls = []

    def fake_get(url, **kwargs):
        urls.append(url)
        if 'weather-maps' in url:
            return response(json_data=api)
        if 'radar_satellite' in url:
            return response(json_data=ims)
        if 'IMSRadar4GIS' in url:
            return response(png('P', 940))
        if 'weather2day' in url:
            return response(big_png(4), url=f'https://www.weather2day.co.il/pics/radar/{unix(created) - 600}.png')
        if 'openstreetmap' in url:
            return response(png('RGB', 256, blob=False))
        return response(png())
    monkeypatch.setattr(requests, 'get', fake_get)

    alert = fake_alert(created)
    diagnostics = {'should_alert': True, 'reason': 'approaching', 'expected_at': created + timedelta(minutes=15),
                   'thresholds': {'MIN_DBZ': 30}}
    alert_dir = capture.snapshot_alert(alert, diagnostics)

    assert alert_dir == str(dirs.alerts / '42')
    files = sorted(os.listdir(alert_dir))
    expected = [f'rv_20260408T{hm}Z.png' for hm in ('0600', '0610', '0620', '0630', '0640', '0650')]
    assert [f for f in files if f.startswith('rv_2')] == expected
    assert len([f for f in files if f.startswith('rv_raw_')]) == 6
    ims_display = [f for f in files if f.startswith('ims_2')]
    assert len(ims_display) == 12 and ims_display[0] == 'ims_20260408T0555Z.png'  # 08:55 IDT
    assert ims_display[-1] == 'ims_20260408T0650Z.png'
    assert len([f for f in files if f.startswith('ims_raw_')]) == 12
    assert 'w2d_20260408T0644Z.png' in files
    assert 'preview.png' in files
    with open(os.path.join(alert_dir, 'preview.png'), 'rb') as f, \
            open(os.path.join(alert_dir, ims_display[-1]), 'rb') as g:
        assert f.read() == g.read()  # preview = latest IMS frame
    assert any('/2/0_0.png' in u for u in urls) and any('/2/1_0.png' in u for u in urls)
    assert any('/512/7/32.0828/34.8101/' in u for u in urls)  # centred on the location
    assert os.listdir(dirs.radar / 'basemap')  # basemap tiles cached

    with open(os.path.join(alert_dir, 'detection.json')) as f:
        doc = json.load(f)
    assert doc['window_utc'] == {'start': '2026-04-08T05:54:10Z', 'end': '2026-04-08T06:54:10Z'}
    assert doc['alert']['created_at'] == '2026-04-08T06:54:10Z'
    assert doc['diagnostics']['expected_at'] == '2026-04-08T07:09:10Z'
    assert doc['thresholds'] == {'MIN_DBZ': 30}
    assert doc['location']['latitude'] == 32.0828
    assert len(doc['files']['rainviewer']) == 6 and len(doc['files']['ims']) == 12
    assert doc['errors'] == []
    assert alert.radar_images_saved == 'alerts/42'

    info = capture.load_capture(42)
    assert info['anim'][0] == ('ims_20260408T0555Z.png', '05:55')
    assert [title for title, _ in info['strips']] == ['IMS radar', 'RainViewer', 'weather2day']
    assert info['raw_count'] == 18
    assert info['summary']['reason'] == 'approaching'


def test_snapshot_alert_survives_network_failure(dirs, monkeypatch):
    def boom(url, **kwargs):
        raise requests.ConnectionError('offline')
    monkeypatch.setattr(requests, 'get', boom)
    created = datetime(2026, 4, 8, 6, 54)
    # A buffered Israel-centred frame in the window is used as the fallback
    (dirs.radar / 'radar_20260408T0640Z.png').write_bytes(png())

    started = time.monotonic()
    alert_dir = capture.snapshot_alert(fake_alert(created, alert_id=43), None, budget_s=5)
    assert time.monotonic() - started < 10
    files = os.listdir(alert_dir)
    assert 'rv_20260408T0640Z.png' in files and 'preview.png' in files
    with open(os.path.join(alert_dir, 'detection.json')) as f:
        doc = json.load(f)
    assert doc['diagnostics'] is None
    assert doc['files']['rainviewer'][0]['source'] == 'buffer'
    assert any('offline' in e for e in doc['errors'])


def test_render_frame_marks_location_at_centre():
    img = capture.render_frame(png(blob=False), 32.08, 34.81, datetime(2026, 4, 8, 6, 50))
    c = capture.OUT_PX // 2
    assert img.getpixel((c, c)) == (255, 0, 0)


def test_parse_stamp_round_trip():
    assert parse_stamp('rv_raw_20260408T0650Z.png') == datetime(2026, 4, 8, 6, 50)
    assert parse_stamp('radar_202604080650.png') is None
