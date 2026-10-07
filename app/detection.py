"""
Rain detection from radar reflectivity (pure functions, no network I/O).

Input: a time series of reflectivity grids (dBZ, NaN = no echo) around one
location, plus per-pixel distance/bearing from that location.
Output: a diagnostics dict saying whether to alert and why.

Every tunable threshold is in the block below.
"""
import math
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np

# --------------------------------------------------------------------------
# Tunable thresholds (all detection behaviour is controlled from here)
# --------------------------------------------------------------------------
RAIN_DBZ = 20                 # pixel counts as rain at/above this (~0.6 mm/h, Marshall-Palmer)
ALERT_MIN_DBZ = 25            # the rain cell that triggers an alert must peak at/above this (~1.3 mm/h)
MIN_CELL_PIXELS = 4           # ignore connected rain areas smaller than this (speckle; ~1 km2 per pixel)
CLUTTER_MIN_FRAMES = 6        # stationary-clutter filter needs at least this many valid frames
CLUTTER_MAX_DBZ_SPREAD = 2    # rain in EVERY frame with max-min dBZ <= this => stationary clutter
ANALYSIS_RADIUS_KM = 60       # only rain within this radius is considered
AT_LOCATION_KM = 3            # rain this close counts as "at the location"
AT_LOCATION_CONFIRM_KM = 15   # ...and the previous frame must have had rain within this distance
MOTION_FRAMES = 4             # newest frames used for the approach trend (~30 min at 10-min steps)
MOTION_MIN_FRAMES = 3         # of those, at least this many must contain rain
MIN_APPROACH_KM = 2           # nearest rain must have closed in by at least this over the window
MAX_STEP_RECEDE_KM = 2        # a single step may move away by at most this (noise) and still be "consistent"
MIN_APPROACH_KMH = 5          # slower than this is treated as stationary
MAX_APPROACH_KMH = 120        # faster than this is a different cell popping up, not motion
ALERT_ETA_MINUTES = 30        # alert when rain is expected within this many minutes from now
MAX_FRAME_AGE_MINUTES = 30    # newest radar frame older than this => stale data, never alert
OBSERVATION_LAG_MINUTES = 5   # RainViewer frame T best matches IMS scans from T-5..T-10 min; ETA counts from T-lag
EVENT_CLEAR_KM = 10           # a rain event ends once there is no rain within this distance...
EVENT_CLEAR_MINUTES = 60      # ...for this long; only then can a new alert fire
EVENT_MAX_SUPPRESS_HOURS = 6  # never suppress for longer than this after the last alert
FRAME_STEP_MINUTES = 10       # RainViewer publishes a frame every 10 minutes

THRESHOLDS = {k: v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, (int, float))}

# --------------------------------------------------------------------------
# RainViewer "Universal Blue" palette (colour scheme 2, the only one the free
# API serves since 2026-01). RGBA hex for dBZ -10..95 in 1-dBZ steps, from
# https://www.rainviewer.com/files/rainviewer_api_colors_table.csv (rain rows).
# Tiles must be requested with smoothing off (options "0_0") so pixels are
# exact palette colours.
# --------------------------------------------------------------------------
PALETTE_MIN_DBZ = -10
_UNIVERSAL_BLUE = """
63615914 66635a19 69665c1e 6c685d24 6f6b5f29 726e612e 75706234 78736439
7c75653e 7f786744 827b6949 857d6a4e 88806c54 8b826d59 8e856f5e 92887164
9e93756e aa9e7978 b6a97e82 c2b4828c cec08796 d2c48ba0 d6c88faa dacc93b4
ded097be 88ddeeff 6cd1ebff 51c5e8ff 36bae5ff 1baee2ff 00a3e0ff 009ad5ff
0091caff 0088bfff 007fb4ff 0077aaff 0070a3ff 00699cff 006295ff 005b8eff
005588ff 005180ff 004e78ff 004a70ff 004768ff ffee00ff ffe000ff ffd200ff
ffc500ff ffb700ff ffaa00ff ff9f00ff ff9500ff ff8b00ff ff8100ff ff4400ff
f23600ff e62800ff d91b00ff cd0d00ff c10000ff a80000ff 8f0000ff 760000ff
5d0000ff ffaaffff ff9fffff ff95ffff ff8bffff ff81ffff ff77ffff ff6cffff
ff62ffff ff58ffff ff4effff ffffffff ffffffff ffffffff ffffffff ffffffff
ffffffff ffffffff ffffffff ffffffff ffffffff 00ff00ff
""".split()  # noqa: SIM905


def _build_lut():
    keys, values = [], []
    for i, hex_rgba in enumerate(_UNIVERSAL_BLUE):
        key = int(hex_rgba, 16)
        if key not in keys:  # repeated colours (e.g. white 65-74) decode to the lowest dBZ
            keys.append(key)
            values.append(PALETTE_MIN_DBZ + i)
    order = np.argsort(keys)
    return np.array(keys, dtype=np.uint32)[order], np.array(values, dtype=np.float32)[order]


_LUT_KEYS, _LUT_DBZ = _build_lut()


def dbz_to_rgba(dbz: int) -> tuple:
    """Palette colour for a dBZ value (used to build synthetic tiles in tests)."""
    v = int(_UNIVERSAL_BLUE[int(dbz) - PALETTE_MIN_DBZ], 16)
    return (v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255


def decode_rgba(rgba: np.ndarray):
    """
    Decode an (H, W, 4) uint8 RGBA tile to dBZ.
    Returns (dbz float32 array with NaN for no echo, number of opaque pixels
    whose colour is not in the palette). Unknown colours decode to NaN; a high
    unknown count means the tile is not radar data (e.g. a placeholder image).
    """
    rgba = rgba.astype(np.uint32)
    packed = (rgba[..., 0] << 24) | (rgba[..., 1] << 16) | (rgba[..., 2] << 8) | rgba[..., 3]
    idx = np.clip(np.searchsorted(_LUT_KEYS, packed), 0, len(_LUT_KEYS) - 1)
    found = _LUT_KEYS[idx] == packed
    dbz = np.where(found, _LUT_DBZ[idx], np.nan).astype(np.float32)
    unknown = int(np.count_nonzero(~found & (rgba[..., 3] > 0)))
    return dbz, unknown


# --------------------------------------------------------------------------
# Geometry (Web Mercator tiles)
# --------------------------------------------------------------------------
DIRECTIONS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')


def lat_lon_to_global_px(lat: float, lon: float, zoom: int, tile_size: int) -> tuple:
    """Fractional global pixel coordinates of a lat/lon at a zoom level."""
    n = (2 ** zoom) * tile_size
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def distance_bearing_grid(lat: float, lon: float, zoom: int, tile_size: int, gx0: int, gy0: int,
                          height: int, width: int):
    """
    Distance (km) and bearing (deg, 0=N, clockwise) from (lat, lon) to the
    centre of every pixel of a mosaic whose top-left global pixel is (gx0, gy0).
    Local equirectangular approximation: well under 1% error within 100 km.
    """
    n = (2 ** zoom) * tile_size
    gx = gx0 + np.arange(width) + 0.5
    gy = gy0 + np.arange(height) + 0.5
    lons = gx / n * 360.0 - 180.0
    lats = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * gy / n))))
    dx = (lons[None, :] - lon) * 111.320 * math.cos(math.radians(lat))
    dy = (lats[:, None] - lat) * 110.574
    dx, dy = np.broadcast_arrays(dx, dy)
    dist = np.hypot(dx, dy).astype(np.float32)
    bearing = (np.degrees(np.arctan2(dx, dy)) % 360.0).astype(np.float32)
    return dist, bearing


def compass(bearing_deg) -> str | None:
    if bearing_deg is None:
        return None
    return DIRECTIONS[int(((bearing_deg % 360) + 22.5) // 45) % 8]


def intensity_label(dbz) -> str:
    if dbz is None or dbz < 30:
        return 'Light'       # < ~2.7 mm/h
    if dbz < 40:
        return 'Moderate'    # ~2.7-11 mm/h
    if dbz < 50:
        return 'Heavy'       # ~11-50 mm/h
    return 'Very heavy'


# --------------------------------------------------------------------------
# Per-frame analysis
# --------------------------------------------------------------------------
def label_cells(mask: np.ndarray) -> np.ndarray:
    """8-connected component labels (0 = background). Pure Python BFS; grids are small."""
    labels = np.zeros(mask.shape, dtype=np.int32)
    h, w = mask.shape
    current = 0
    for y0, x0 in zip(*np.nonzero(mask)):
        if labels[y0, x0]:
            continue
        current += 1
        labels[y0, x0] = current
        queue = deque([(y0, x0)])
        while queue:
            y, x = queue.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = current
                        queue.append((ny, nx))
    return labels


def clutter_mask(dbz_frames: list) -> np.ndarray | None:
    """
    Pixels that are rain in every frame with an almost constant value: ground
    clutter, not weather. Returns None when there are too few frames to judge.
    """
    valid = [d for d in dbz_frames if d is not None]
    if len(valid) < CLUTTER_MIN_FRAMES:
        return None
    stack = np.nan_to_num(np.stack(valid), nan=-99.0)
    always = np.all(stack >= RAIN_DBZ, axis=0)
    spread = stack.max(axis=0) - stack.min(axis=0)
    return always & (spread <= CLUTTER_MAX_DBZ_SPREAD)


def frame_stats(dbz: np.ndarray, dist: np.ndarray, bearing: np.ndarray, clutter=None) -> dict:
    """Nearest rain distance/bearing, intensities and pixel counts for one frame."""
    with np.errstate(invalid='ignore'):
        rain = (dbz >= RAIN_DBZ) & (dist <= ANALYSIS_RADIUS_KM)
    clutter_px = 0
    if clutter is not None:
        clutter_px = int(np.count_nonzero(rain & clutter))
        rain &= ~clutter
    labels = label_cells(rain)
    sizes = np.bincount(labels.ravel())
    small = np.nonzero(sizes < MIN_CELL_PIXELS)[0]
    small = small[small > 0]
    speckle_px = int(sizes[small].sum()) if len(small) else 0
    if len(small):
        labels[np.isin(labels, small)] = 0
    rain = labels > 0

    stats = {
        'nearest_km': None, 'bearing_deg': None, 'direction': None,
        'nearest_cell_max_dbz': None, 'max_dbz': None,
        'rain_px': int(np.count_nonzero(rain)), 'cells': len(np.unique(labels[rain])),
        'speckle_px': speckle_px, 'clutter_px': clutter_px,
    }
    if not stats['rain_px']:
        return stats
    masked = np.where(rain, dist, np.inf)
    iy, ix = np.unravel_index(np.argmin(masked), masked.shape)
    cell = labels == labels[iy, ix]
    stats.update({
        'nearest_km': round(float(dist[iy, ix]), 1),
        'bearing_deg': round(float(bearing[iy, ix])),
        'direction': compass(float(bearing[iy, ix])),
        'nearest_cell_max_dbz': float(np.nanmax(dbz[cell])),
        'max_dbz': float(np.nanmax(dbz[rain])),
    })
    return stats


# --------------------------------------------------------------------------
# Motion and decision
# --------------------------------------------------------------------------
def estimate_motion(frames: list) -> dict:
    """
    Approach trend of the nearest rain over the newest MOTION_FRAMES frames.
    `frames` are frame-stat dicts (oldest first) with 'epoch' and 'nearest_km'.
    """
    window = [f for f in frames if not f.get('error')][-MOTION_FRAMES:]
    pts = [(f['epoch'] / 60.0, f['nearest_km']) for f in window if f.get('nearest_km') is not None]
    result = {'approaching': False, 'consistent': False, 'velocity_kmh': None, 'eta_from_frame_min': None,
              'points': [round(d, 1) for _, d in pts], 'reason': ''}
    if not window or window[-1].get('nearest_km') is None:
        result['reason'] = 'no rain in newest frame'
        return result
    if len(pts) < MOTION_MIN_FRAMES:
        result['reason'] = f'rain in only {len(pts)} of last {len(window)} frames'
        return result
    t = np.array([p[0] for p in pts])
    d = np.array([p[1] for p in pts])
    slope = float(np.polyfit(t - t[0], d, 1)[0])  # km per minute (negative = approaching)
    speed = -slope * 60.0
    result['velocity_kmh'] = round(speed, 1)
    closed = d[0] - d[-1]
    max_recede = float(np.max(np.diff(d))) if len(d) > 1 else 0.0
    if closed < MIN_APPROACH_KM:
        result['reason'] = f'not closing in (moved {closed:+.1f} km toward location)'
    elif max_recede > MAX_STEP_RECEDE_KM:
        result['reason'] = f'inconsistent trend (one step receded {max_recede:.1f} km)'
    elif speed < MIN_APPROACH_KMH:
        result['reason'] = f'approach too slow ({speed:.0f} km/h)'
    elif speed > MAX_APPROACH_KMH:
        result['reason'] = f'implausible approach speed ({speed:.0f} km/h), likely new cells'
    else:
        result.update(approaching=True, consistent=True,
                      eta_from_frame_min=round(float(d[-1]) / speed * 60.0, 1),
                      reason=f'approaching at {speed:.0f} km/h')
    return result


def utcnow() -> datetime:
    """Naive UTC now (matches the DB's datetime.utcnow convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def epoch_to_utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def decide(frames: list, now: datetime | None = None) -> dict:
    """
    Alert decision from frame stats (oldest first). Always returns a dict with
    should_alert, reason and the contract fields (None when not applicable).
    """
    now = now or utcnow()
    out = {
        'should_alert': False, 'reason': '', 'minutes_until_rain': None, 'expected_at': None,
        'intensity': None, 'intensity_label': None, 'confidence': None, 'current_distance_km': None,
        'approaching': False, 'velocity_kmh': None, 'bearing_deg': None, 'direction': None,
        'max_dbz': None, 'latest_frame_time': None, 'frame_age_min': None, 'motion': None,
    }
    valid = [f for f in frames if not f.get('error')]
    if not valid:
        out['reason'] = 'no usable radar frames'
        return out
    latest = valid[-1]
    frame_time = epoch_to_utc(latest['epoch'])
    age = (now - frame_time).total_seconds() / 60.0
    out.update(latest_frame_time=frame_time.isoformat(), frame_age_min=round(age, 1),
               current_distance_km=latest['nearest_km'], bearing_deg=latest['bearing_deg'],
               direction=latest['direction'], max_dbz=latest['max_dbz'],
               intensity=latest['nearest_cell_max_dbz'],
               intensity_label=intensity_label(latest['nearest_cell_max_dbz'])
               if latest['nearest_cell_max_dbz'] is not None else None)
    if frames[-1].get('error'):
        out['reason'] = f"newest frame failed: {frames[-1]['error']}"
        return out
    if age > MAX_FRAME_AGE_MINUTES:
        out['reason'] = f'radar data stale ({age:.0f} min old)'
        return out
    if latest['nearest_km'] is None:
        out['reason'] = f'no rain >= {RAIN_DBZ} dBZ within {ANALYSIS_RADIUS_KM} km'
        return out

    cell_dbz = latest['nearest_cell_max_dbz']
    weak = cell_dbz < ALERT_MIN_DBZ
    where = f"{latest['nearest_km']:.0f} km {latest['direction']}"

    if latest['nearest_km'] <= AT_LOCATION_KM:
        prev = valid[-2] if len(valid) >= 2 else None
        confirmed = prev is not None and prev['nearest_km'] is not None \
            and prev['nearest_km'] <= AT_LOCATION_CONFIRM_KM
        if weak:
            out['reason'] = f'rain at location but weak ({cell_dbz:.0f} < {ALERT_MIN_DBZ} dBZ)'
        elif not confirmed:
            out['reason'] = 'rain at location in newest frame only (unconfirmed by previous frame)'
        else:
            out.update(should_alert=True, minutes_until_rain=0, expected_at=now, confidence='high',
                       approaching=True, reason=f'rain at location ({cell_dbz:.0f} dBZ)')
        return out

    motion = estimate_motion(frames)
    out['motion'] = motion
    out['velocity_kmh'] = motion['velocity_kmh']
    if not motion['approaching']:
        out['reason'] = f"rain {where}: {motion['reason']}"
        return out
    out['approaching'] = True
    observed_at = frame_time - timedelta(minutes=OBSERVATION_LAG_MINUTES)
    expected_at = observed_at + timedelta(minutes=motion['eta_from_frame_min'])
    minutes = max(0.0, (expected_at - now).total_seconds() / 60.0)
    out.update(expected_at=max(expected_at, now), minutes_until_rain=round(minutes))
    if minutes > ALERT_ETA_MINUTES:
        out['reason'] = f'rain {where} approaching, ETA {minutes:.0f} min > {ALERT_ETA_MINUTES}'
        return out
    if weak:
        out['reason'] = f'rain {where} approaching but weak ({cell_dbz:.0f} < {ALERT_MIN_DBZ} dBZ)'
        return out
    n_points = len(motion['points'])
    out.update(should_alert=True,
               confidence='high' if n_points >= MOTION_FRAMES and minutes <= 15 else 'medium',
               reason=f"rain {where} approaching at {motion['velocity_kmh']:.0f} km/h, ETA {minutes:.0f} min")
    return out


def is_new_event(frames: list, last_alert_at: datetime | None, now: datetime | None = None) -> tuple:
    """
    Event-based suppression. After an alert, a new one may fire only once there
    has been no rain within EVENT_CLEAR_KM for EVENT_CLEAR_MINUTES (judged from
    the radar frames after the last alert), or EVENT_MAX_SUPPRESS_HOURS passed.
    Frames that failed to load break a quiet run (unknown is not quiet).
    Returns (allowed: bool, reason: str).
    """
    now = now or utcnow()
    if last_alert_at is None:
        return True, 'no previous alert'
    if now - last_alert_at >= timedelta(hours=EVENT_MAX_SUPPRESS_HOURS):
        return True, f'last alert over {EVENT_MAX_SUPPRESS_HOURS} h ago'
    best = run = 0
    for f in frames:
        if epoch_to_utc(f['epoch']) <= last_alert_at:
            continue
        quiet = not f.get('error') and (f.get('nearest_km') is None or f['nearest_km'] > EVENT_CLEAR_KM)
        run = run + 1 if quiet else 0
        best = max(best, run)
    quiet_minutes = best * FRAME_STEP_MINUTES
    if quiet_minutes >= EVENT_CLEAR_MINUTES:
        return True, f'new event ({quiet_minutes} min without rain within {EVENT_CLEAR_KM} km since last alert)'
    return False, (f'same rain event as alert at {last_alert_at:%H:%M} UTC '
                   f'(longest clear spell since: {quiet_minutes} min < {EVENT_CLEAR_MINUTES})')


def build_message(address: str, result: dict) -> str:
    """Human-readable alert text, e.g. 'Moderate rain approaching X from SW, ~12 km, ETA ~15 min'."""
    label = result.get('intensity_label') or intensity_label(result.get('intensity'))
    if result.get('minutes_until_rain') == 0 and (result.get('current_distance_km') or 0) <= AT_LOCATION_KM:
        return f"🌧️ {label} rain at {address} now"
    direction = result.get('direction')
    frm = f" from {direction}" if direction else ""
    dist = result.get('current_distance_km')
    dist_txt = f", ~{dist:.0f} km" if dist is not None else ""
    return f"⚠️ {label} rain approaching {address}{frm}{dist_txt}, ETA ~{result.get('minutes_until_rain')} min"
