"""Detection tests with synthetic radar data (no network)."""
import json
import math
import time
from datetime import datetime, timedelta
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from app import detection as d
from app.radar_global import GlobalRadarService as G

LAT, LON = 32.0828, 34.8101  # Ramat Gan
T0 = 1_791_360_000  # arbitrary frame epoch (multiple of 600)


@pytest.fixture(scope='module')
def grid():
    window = G.mosaic_window(LAT, LON)
    dist, bearing = d.distance_bearing_grid(LAT, LON, G.ZOOM_LEVEL, G.TILE_SIZE, *window)
    return window, dist, bearing


def blob(grid, distance_km, bearing_deg, radius_km=4.0, dbz=35.0):
    """dBZ field (NaN elsewhere) with a disc of rain centred distance_km away at bearing_deg."""
    _, dist, bearing = grid
    px = dist * np.sin(np.radians(bearing))
    py = dist * np.cos(np.radians(bearing))
    bx = distance_km * math.sin(math.radians(bearing_deg))
    by = distance_km * math.cos(math.radians(bearing_deg))
    out = np.full(dist.shape, np.nan, dtype=np.float32)
    out[np.hypot(px - bx, py - by) <= radius_km] = dbz
    return out


def frames_from(grid, fields, clutter=None, start=T0):
    _, dist, bearing = grid
    frames = []
    for i, f in enumerate(fields):
        entry = {'epoch': start + i * 600}
        entry.update(d.frame_stats(f, dist, bearing, clutter))
        frames.append(entry)
    return frames


def now_after(frames, minutes=5):
    return d.epoch_to_utc(frames[-1]['epoch']) + timedelta(minutes=minutes)


# --------------------------------------------------------------- decoding
def test_decode_palette_roundtrip():
    values = [-10, 0, 14, 15, 20, 25, 34, 35, 44, 45, 54, 55, 64, 65, 75]
    rgba = np.zeros((2, len(values), 4), dtype=np.uint8)  # row 1 stays transparent
    for i, v in enumerate(values):
        rgba[0, i] = d.dbz_to_rgba(v)
    dbz, unknown = d.decode_rgba(rgba)
    assert unknown == 0
    assert dbz[0].tolist() == values
    assert np.isnan(dbz[1]).all()


def test_decode_unknown_colours_flagged():
    rgba = np.zeros((4, 4, 4), dtype=np.uint8)
    rgba[:2] = (102, 102, 102, 255)  # grey like the "Zoom Level Not Supported" placeholder
    dbz, unknown = d.decode_rgba(rgba)
    assert unknown == 8
    assert np.isnan(dbz).all()


def test_alpha_is_not_intensity():
    # Bug #3: low dBZ is semi-transparent, but strong echoes are all alpha 255 regardless of dBZ.
    assert d.dbz_to_rgba(20)[3] == d.dbz_to_rgba(55)[3] == 255
    assert d.dbz_to_rgba(5)[3] < 120


# --------------------------------------------------------------- geometry
def test_distance_and_bearing_grid(grid):
    (gx0, gy0, _, _), dist, bearing = grid
    cx, cy = d.lat_lon_to_global_px(LAT, LON, G.ZOOM_LEVEL, G.TILE_SIZE)
    iy, ix = int(cy) - gy0, int(cx) - gx0
    assert dist[iy, ix] < 1.0  # location's own pixel
    # 30 px due north / east
    n = (2 ** G.ZOOM_LEVEL) * G.TILE_SIZE
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (gy0 + iy - 30 + 0.5) / n))))
    lon_e = (gx0 + ix + 30 + 0.5) / n * 360 - 180
    assert dist[iy - 30, ix] == pytest.approx(G.haversine_distance(LAT, LON, lat_n, LON), abs=1.0)
    assert dist[iy, ix + 30] == pytest.approx(G.haversine_distance(LAT, LON, LAT, lon_e), abs=1.0)
    assert bearing[iy - 30, ix] == pytest.approx(0, abs=3) or bearing[iy - 30, ix] > 357
    assert bearing[iy, ix + 30] == pytest.approx(90, abs=3)
    assert bearing[iy + 30, ix - 30] == pytest.approx(225, abs=3)
    # window covers the analysis radius in every direction
    assert dist[0, ix] > d.ANALYSIS_RADIUS_KM and dist[iy, 0] > d.ANALYSIS_RADIUS_KM
    assert dist[-1, ix] > d.ANALYSIS_RADIUS_KM and dist[iy, -1] > d.ANALYSIS_RADIUS_KM


def test_compass():
    assert [d.compass(b) for b in (0, 44, 46, 180, 225, 300, 359)] == ['N', 'NE', 'NE', 'S', 'SW', 'NW', 'N']


# --------------------------------------------------------------- frame stats
def test_frame_stats_nearest_bearing_and_filters(grid):
    _, dist, bearing = grid
    f = blob(grid, 20, 225, radius_km=4, dbz=38)
    f[np.isnan(f) & (np.abs(dist - 8) < 0.6) & (np.abs(bearing - 90) < 4)] = 45  # tiny speckle at 8 km E
    f[np.isnan(f) & (np.abs(dist - 5) < 3) & (np.abs(bearing - 0) < 30)] = 15     # weak echo N (< RAIN_DBZ)
    s = d.frame_stats(f, dist, bearing)
    assert s['nearest_km'] == pytest.approx(16, abs=1.5)
    assert s['direction'] == 'SW'
    assert s['nearest_cell_max_dbz'] == 38
    assert s['cells'] == 1 and s['speckle_px'] > 0


def test_no_rain(grid):
    _, dist, bearing = grid
    s = d.frame_stats(np.full(dist.shape, np.nan, np.float32), dist, bearing)
    assert s['nearest_km'] is None and s['rain_px'] == 0


def test_stationary_clutter_rejected(grid):
    clutter_field = blob(grid, 6, 120, radius_km=2, dbz=40)  # identical in every frame
    fields = [clutter_field.copy() for _ in range(8)]
    mask = d.clutter_mask(fields)
    assert mask is not None and mask.sum() > 0
    frames = frames_from(grid, fields, mask)
    assert all(f['nearest_km'] is None for f in frames)
    assert d.decide(frames, now_after(frames))['should_alert'] is False


def test_clutter_filter_keeps_moving_rain(grid):
    fields = [blob(grid, 50 - 5 * i, 260, dbz=35) for i in range(8)]
    mask = d.clutter_mask(fields)
    assert mask.sum() == 0


def test_clutter_filter_needs_enough_frames(grid):
    assert d.clutter_mask([blob(grid, 6, 0)] * (d.CLUTTER_MIN_FRAMES - 1)) is None


# --------------------------------------------------------------- motion / decision
def test_approaching_rain_alerts_with_eta_and_direction(grid):
    # cell from the WSW at 30 km/h (5 km per 10-min frame), edge reaches 12 km in the newest frame
    fields = [blob(grid, 31 - 5 * i, 250, radius_km=4, dbz=36) for i in range(4)]
    frames = frames_from(grid, fields)
    now = now_after(frames, 6)
    r = d.decide(frames, now)
    assert r['should_alert'] is True, r['reason']
    assert r['approaching'] is True
    assert r['velocity_kmh'] == pytest.approx(30, abs=4)
    assert r['current_distance_km'] == pytest.approx(12, abs=1.5)
    expected = 12 / 30 * 60 - 6 - d.OBSERVATION_LAG_MINUTES
    assert r['minutes_until_rain'] == pytest.approx(expected, abs=4)
    assert r['direction'] == 'W' or r['direction'] == 'SW'
    assert r['intensity_label'] == 'Moderate'
    assert r['expected_at'] > now
    msg = d.build_message('Home', r)
    assert msg.startswith('⚠️ Moderate rain approaching Home from ')
    assert '~12 km' in msg and 'ETA ~' in msg


def test_far_approaching_rain_waits(grid):
    fields = [blob(grid, 60 - 4 * i, 270, dbz=40) for i in range(4)]  # 24 km/h, 48 km away -> ETA ~2 h
    frames = frames_from(grid, fields)
    r = d.decide(frames, now_after(frames))
    assert r['should_alert'] is False and 'ETA' in r['reason']


def test_receding_rain_does_not_alert(grid):
    fields = [blob(grid, 12 + 5 * i, 250, dbz=40) for i in range(4)]
    frames = frames_from(grid, fields)
    r = d.decide(frames, now_after(frames))
    assert r['should_alert'] is False
    assert 'not closing in' in r['reason']


def test_stationary_nearby_rain_does_not_alert(grid):
    fields = [blob(grid, 10, 180, dbz=40) for _ in range(4)]
    frames = frames_from(grid, fields)
    assert d.decide(frames, now_after(frames))['should_alert'] is False


def test_single_noisy_pair_is_not_enough(grid):
    nan = np.full(fields_shape(grid), np.nan, np.float32)
    fields = [nan, nan, blob(grid, 25, 240), blob(grid, 14, 240)]
    frames = frames_from(grid, fields)
    r = d.decide(frames, now_after(frames))
    assert r['should_alert'] is False and 'only 2' in r['reason']


def test_inconsistent_jumps_do_not_alert(grid):
    # nearest rain jumps around (different cells appearing/disappearing)
    fields = [blob(grid, x, 200, dbz=40) for x in (30, 15, 28, 14)]
    frames = frames_from(grid, fields)
    r = d.decide(frames, now_after(frames))
    assert r['should_alert'] is False


def test_weak_rain_approaching_does_not_alert(grid):
    fields = [blob(grid, 28 - 5 * i, 250, dbz=d.ALERT_MIN_DBZ - 3) for i in range(4)]
    frames = frames_from(grid, fields)
    r = d.decide(frames, now_after(frames))
    assert r['should_alert'] is False and 'weak' in r['reason']


def test_rain_at_location(grid):
    fields = [blob(grid, 10, 250, radius_km=5, dbz=42), blob(grid, 1, 250, radius_km=5, dbz=42)]
    frames = frames_from(grid, fields)
    now = now_after(frames)
    r = d.decide(frames, now)
    assert r['should_alert'] is True and r['minutes_until_rain'] == 0
    assert r['intensity_label'] == 'Heavy'
    assert d.build_message('Home', r) == '🌧️ Heavy rain at Home now'


def test_rain_at_location_needs_previous_frame(grid):
    nan = np.full(fields_shape(grid), np.nan, np.float32)
    frames = frames_from(grid, [nan, blob(grid, 0, 0, radius_km=3, dbz=45)])
    r = d.decide(frames, now_after(frames))
    assert r['should_alert'] is False and 'unconfirmed' in r['reason']


def test_stale_data_never_alerts(grid):
    fields = [blob(grid, 10, 250, radius_km=5), blob(grid, 0, 250, radius_km=5)]
    frames = frames_from(grid, fields)
    r = d.decide(frames, now_after(frames, d.MAX_FRAME_AGE_MINUTES + 5))
    assert r['should_alert'] is False and 'stale' in r['reason']


def test_failed_newest_frame_never_alerts(grid):
    fields = [blob(grid, 10, 250, radius_km=5), blob(grid, 0, 250, radius_km=5)]
    frames = frames_from(grid, fields)
    frames.append({'epoch': frames[-1]['epoch'] + 600, 'error': 'timeout'})
    r = d.decide(frames, now_after(frames))
    assert r['should_alert'] is False and 'failed' in r['reason']


def test_no_frames():
    r = d.decide([])
    assert r['should_alert'] is False and r['reason']


def fields_shape(grid):
    return grid[1].shape


# --------------------------------------------------------------- event suppression
def _quiet_rain_frames(pattern):
    """pattern: list of nearest_km (None = no rain), one per 10-min frame starting at T0."""
    return [{'epoch': T0 + i * 600, 'nearest_km': km} for i, km in enumerate(pattern)]


def test_event_suppression():
    frames = _quiet_rain_frames([5, 3, 0, 0, 2, 8, 0])
    alert_at = d.epoch_to_utc(T0) + timedelta(minutes=5)
    now = d.epoch_to_utc(frames[-1]['epoch']) + timedelta(minutes=5)
    assert d.is_new_event(frames, None, now)[0] is True
    allowed, reason = d.is_new_event(frames, alert_at, now)
    assert allowed is False and 'same rain event' in reason
    # an hour (6 frames) without rain within EVENT_CLEAR_KM after the alert => new event
    frames = _quiet_rain_frames([2, None, 30, None, 25, 20, None, 12, 6])
    assert d.is_new_event(frames, alert_at, now + timedelta(minutes=30))[0] is True
    # a failed frame breaks the quiet spell (unknown is not quiet)
    frames = _quiet_rain_frames([2, None, None, None, None, None, None, 6])
    frames[3]['error'] = 'timeout'
    assert d.is_new_event(frames, alert_at, now)[0] is False
    # hard cap
    assert d.is_new_event(frames, alert_at, alert_at + timedelta(hours=d.EVENT_MAX_SUPPRESS_HOURS))[0] is True


# --------------------------------------------------------------- end-to-end with fake HTTP
class FakeResponse:
    def __init__(self, status=200, content=b'', payload=None):
        self.status_code = status
        self.content = content
        self._payload = payload

    def json(self):
        return self._payload


def _png(rgba):
    buf = BytesIO()
    Image.fromarray(rgba, 'RGBA').save(buf, 'PNG')
    return buf.getvalue()


@pytest.fixture
def fake_rainviewer(monkeypatch, grid):
    """Serves 4 frames of a cell approaching Ramat Gan from the W at 30 km/h, rendered into real tiles."""
    gx0, gy0, h, w = grid[0]
    ts = G.TILE_SIZE
    now = int(time.time())
    latest = now // 600 * 600
    if now - latest < 300:
        latest -= 600
    times = [latest - 600 * (3 - i) for i in range(4)]
    fields = {f'/v2/radar/f{i}': blob(grid, 31 - 5 * i, 270, radius_km=4, dbz=40) for i in range(4)}
    calls = {'n': 0, 'fail': False, 'placeholder': False}

    def tile_png(path, tx, ty):
        rgba = np.zeros((ts, ts, 4), dtype=np.uint8)
        if calls['placeholder']:
            rgba[:] = (102, 102, 102, 255)
            return _png(rgba)
        field = fields[path]
        for y in range(ts):
            gy = ty * ts + y - gy0
            if not 0 <= gy < h:
                continue
            for x in range(ts):
                gx = tx * ts + x - gx0
                if 0 <= gx < w and not np.isnan(field[gy, gx]):
                    rgba[y, x] = d.dbz_to_rgba(field[gy, gx])
        return _png(rgba)

    def fake_get(url, timeout=None):
        calls['n'] += 1
        assert timeout is not None
        if calls['fail']:
            raise ConnectionError('network down')
        if url == G.API_URL:
            past = [{'time': t, 'path': f'/v2/radar/f{i}'} for i, t in enumerate(times)]
            return FakeResponse(payload={'host': 'https://tiles.test', 'radar': {'past': past, 'nowcast': []}})
        rest = url[len('https://tiles.test'):]
        parts = rest.split('/')  # ['', 'v2', 'radar', 'fN', size, z, x, y, color, opts]
        path = '/'.join(parts[:4])
        assert parts[4:6] == [str(ts), str(G.ZOOM_LEVEL)] and parts[8] == '2' and parts[9] == '0_0.png'
        return FakeResponse(content=tile_png(path, int(parts[6]), int(parts[7])))

    monkeypatch.setattr('app.radar_global.requests.get', fake_get)
    G._tile_cache.clear()
    yield calls
    G._tile_cache.clear()


def test_analyze_location_end_to_end(fake_rainviewer):
    r = G.analyze_location(LAT, LON)
    assert r['should_alert'] is True, r['reason']
    assert r['direction'] == 'W' and r['source'] == 'rainviewer'
    assert len(r['frames']) == 4 and not r['errors']
    tiles_per_frame = (r['http_requests'] - 1) // 4
    assert 1 <= tiles_per_frame <= 4  # each frame's tiles fetched once (bug #7: was ~36 per frame)
    assert G.check_rain_at_location(LAT, LON) is not None
    json.dumps(r, default=str)  # diagnostics must be loggable (expected_at is a datetime)
    assert isinstance(r['expected_at'], datetime) and r['expected_at'].tzinfo is None
    # second check: frames are cached, only the API call goes out
    before = fake_rainviewer['n']
    G.analyze_location(LAT, LON)
    assert fake_rainviewer['n'] - before == 1


def test_analyze_location_network_failure_is_safe(fake_rainviewer):
    fake_rainviewer['fail'] = True
    r = G.analyze_location(LAT, LON)
    assert r['should_alert'] is False
    assert 'failed' in r['reason'] and r['errors']
    assert G.check_rain_at_location(LAT, LON) is None


def test_analyze_location_rejects_placeholder_tiles(fake_rainviewer):
    fake_rainviewer['placeholder'] = True
    r = G.analyze_location(LAT, LON)
    assert r['should_alert'] is False
    assert any('not radar data' in e for e in r['errors'])
