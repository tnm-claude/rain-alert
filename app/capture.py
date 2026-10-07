"""
Alert data capture, for tuning the detector after the fact.

- snapshot_alert(alert, diagnostics) writes data/alerts/<alert_id>/ with the IMS and RainViewer
  radar frames from the 60 min before the alert, preview.png and detection.json
- prune() keeps data/alerts under 500 MB (oldest unlabelled alerts go first)
- log_check(location, diagnostics) records one detection_checks row per location per check

Everything here is best-effort: failures are logged, never raised to the caller.
All times are naive UTC, like Alert.created_at.
"""
import contextlib
import json
import math
import numbers
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageOps

from app.models import Alert, DetectionCheck, db
from app.radar import (
    RadarService,
    is_valid_tile,
    parse_stamp,
    to_utc_naive,
    utc_from_unix,
    utc_now,
    utc_stamp,
)

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
ALERTS_DIR = os.path.join(DATA_DIR, 'alerts')

WINDOW = timedelta(minutes=60)          # radar history saved per alert
MAX_ALERTS_BYTES = 500 * 1024 * 1024    # cap for data/alerts
CHECK_RETENTION_DAYS = 180              # detection_checks rows
MAINTENANCE_INTERVAL = timedelta(hours=24)
TIME_BUDGET_S = 20                      # total wall time for one snapshot
HTTP_TIMEOUT = 8
DETAILS_MAX_CHARS = 1000                # detection_checks.details (ASCII JSON, so chars == bytes)

# RainViewer frames: 512px coordinate tile at z7 (max zoom) centred on the location,
# ~265 km across, ~0.52 km/px. Display frames are cropped to CROP_KM and upscaled.
ZOOM = RadarService.ZOOM_LEVEL
TILE_SIZE = RadarService.TILE_SIZE
DISPLAY_SCHEME = '2/1_0'  # colour scheme 2 (display palette), smoothed, no snow
RAW_SCHEME = '0/0_0'      # colour scheme 0, unsmoothed: the scheme that decodes to dBZ
CROP_KM = 120
OUT_PX = 480
RINGS_KM = (10, 25, 50)
BACKGROUND = (225, 225, 225, 255)
# Basemap under the display frames: OSM tiles, greyed, cached forever in data/radar/basemap
# (a handful of tiles per location, within the OSM tile usage policy). CARTO's free
# basemaps now return 'API KEY REQUIRED' watermark tiles with HTTP 200, so not used.
BASEMAP_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
BASEMAP_ZOOM = 9  # 256px tiles, ~0.26 km/px at 32N, close to OUT_PX / CROP_KM
USER_AGENT = 'rain-alert/1.0 (personal, non-commercial)'
BROWSER_UA = 'Mozilla/5.0 (rain-alert)'

# IMS radar: frame list + 940x940 transparent overlays, linear in Web Mercator over these bounds
IMS_BASE = "https://ims.gov.il"
IMS_API = IMS_BASE + "/he/radar_satellite/1"  # the /he/ prefix is required
IMS_BOUNDS = (31.7662932, 29.4468657, 37.8648132, 34.5318966)  # west, south, east, north
ISRAEL_TZ = ZoneInfo('Asia/Jerusalem')

_last_maintenance = None


# ---------- helpers ----------

def iso_utc(dt):
    dt = to_utc_naive(dt)
    return dt.isoformat(timespec='seconds') + 'Z' if dt else None


def _json_default(o):
    if isinstance(o, datetime):
        return iso_utc(o)
    if isinstance(o, date):
        return o.isoformat()
    if hasattr(o, 'tolist'):  # numpy arrays and scalars
        return o.tolist()
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    return str(o)


def _dumps(obj, **kwargs):
    return json.dumps(obj, default=_json_default, **kwargs)


def frames_in_window(frames, alert_time, window=WINDOW):
    """RainViewer `past` frames ({time: unix, path}) with alert_time - window <= t <= alert_time.
    Returns [(naive UTC datetime, frame)], oldest first."""
    end = to_utc_naive(alert_time)
    start = end - window
    selected = []
    for frame in frames or []:
        if not frame.get('time') or not frame.get('path'):
            continue
        ts = utc_from_unix(frame['time'])
        if start <= ts <= end:
            selected.append((ts, frame))
    return sorted(selected, key=lambda x: x[0])


def _world_px(lat, lon, zoom=ZOOM, tile_size=TILE_SIZE):
    n = tile_size * 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def km_per_px(lat):
    return 40075.016686 * math.cos(math.radians(lat)) / (TILE_SIZE * 2 ** ZOOM)


def basemap(lat, lon, deadline=None):
    """OUT_PX x OUT_PX RGBA map covering CROP_KM around (lat, lon), or None.
    Tiles are cached in data/radar/basemap/ (they do not change), so this is one-off per area."""
    try:
        cache = os.path.join(RadarService.get_radar_directory(), 'basemap')
        os.makedirs(cache, exist_ok=True)
        wx, wy = _world_px(lat, lon, BASEMAP_ZOOM, 256)
        half = CROP_KM / 2 / (40075.016686 * math.cos(math.radians(lat)) / (256 * 2 ** BASEMAP_ZOOM))
        x0, y0 = int((wx - half) // 256), int((wy - half) // 256)
        x1, y1 = int((wx + half) // 256), int((wy + half) // 256)
        mosaic = Image.new('RGBA', ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256), BACKGROUND)
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                path = os.path.join(cache, f"{BASEMAP_ZOOM}_{tx}_{ty}.png")
                if not os.path.exists(path):
                    timeout = _timeout(deadline) if deadline else HTTP_TIMEOUT
                    resp = requests.get(BASEMAP_URL.format(z=BASEMAP_ZOOM, x=tx, y=ty), timeout=timeout,
                                        headers={'User-Agent': USER_AGENT})
                    resp.raise_for_status()
                    Image.open(BytesIO(resp.content)).verify()
                    with open(path, 'wb') as f:
                        f.write(resp.content)
                mosaic.paste(Image.open(path).convert('RGBA'), ((tx - x0) * 256, (ty - y0) * 256))
        ox, oy = wx - x0 * 256, wy - y0 * 256
        box = tuple(round(v) for v in (ox - half, oy - half, ox + half, oy + half))
        grey_map = ImageOps.grayscale(mosaic.crop(box).resize((OUT_PX, OUT_PX), Image.BILINEAR))
        return Image.blend(grey_map, Image.new('L', grey_map.size, 255), 0.3).convert('RGBA')
    except Exception as e:
        print(f"[Capture] Basemap unavailable: {e}")
        return None


def _compose(overlay, box, when, background=None):
    """Crop `overlay` (RGBA) to `box`, scale to OUT_PX over the basemap, and draw range rings,
    the location marker (always the centre) and the UTC time."""
    radar = overlay.crop(box).resize((OUT_PX, OUT_PX), Image.NEAREST)
    img = background.copy() if background is not None else Image.new('RGBA', (OUT_PX, OUT_PX), BACKGROUND)
    img.alpha_composite(radar)
    draw = ImageDraw.Draw(img)
    c, scale = OUT_PX / 2, OUT_PX / CROP_KM
    grey = (70, 70, 70, 255)
    for r in RINGS_KM:
        draw.ellipse([c - r * scale, c - r * scale, c + r * scale, c + r * scale], outline=grey)
        draw.text((c + 3, c - r * scale + 2), f"{r} km", fill=grey)
    red, white = (255, 0, 0, 255), (255, 255, 255, 255)
    for x0, y0, x1, y1 in ((c - 20, c, c - 9, c), (c + 9, c, c + 20, c),
                           (c, c - 20, c, c - 9), (c, c + 9, c, c + 20)):
        draw.line([x0, y0, x1, y1], fill=red, width=3)
    draw.ellipse([c - 8, c - 8, c + 8, c + 8], outline=white, width=3)
    draw.ellipse([c - 5, c - 5, c + 5, c + 5], fill=red)
    draw.rectangle([0, 0, 150, 15], fill=(0, 0, 0, 255))
    draw.text((4, 2), f"{to_utc_naive(when):%Y-%m-%d %H:%M} UTC", fill=white)
    draw.text((OUT_PX - 12, 4), "N", fill=grey)
    if background is not None:
        draw.text((OUT_PX - 180, OUT_PX - 13), "(c) OpenStreetMap contributors", fill=grey)
    return img.convert('RGB')


def render_frame(tile_png, lat, lon, when, center=None, background=None):
    """Display frame from a RainViewer coordinate tile, CROP_KM around (lat, lon).
    `center` is the tile's centre if it is not (lat, lon); `background` comes from basemap()."""
    tile = Image.open(BytesIO(tile_png)).convert('RGBA')
    size = tile.width
    wx, wy = _world_px(lat, lon)
    cx, cy = _world_px(*(center or (lat, lon)))
    px, py = size / 2 + (wx - cx) * size / TILE_SIZE, size / 2 + (wy - cy) * size / TILE_SIZE
    half = CROP_KM / 2 / km_per_px(lat) * size / TILE_SIZE
    box = tuple(round(v) for v in (px - half, py - half, px + half, py + half))
    return _compose(tile, box, when, background)


def _merc_y(lat):
    return math.asinh(math.tan(math.radians(lat)))


def save_display(img, path):
    """Save a display frame as a 64-colour PNG (~40-60 KB with the basemap instead of ~150 KB)."""
    img.quantize(colors=64, method=Image.Quantize.FASTOCTREE).save(path, optimize=True)


def in_ims_coverage(lat, lon):
    """IMS overlays only cover Israel and surroundings (IMS_BOUNDS)."""
    west, south, east, north = IMS_BOUNDS
    return south <= lat <= north and west <= lon <= east


def ims_pixel(lat, lon, width, height):
    """Pixel of (lat, lon) in an IMS overlay (a linear stretch in Web Mercator over IMS_BOUNDS)."""
    west, south, east, north = IMS_BOUNDS
    px = (lon - west) / (east - west) * width
    py = (_merc_y(north) - _merc_y(lat)) / (_merc_y(north) - _merc_y(south)) * height
    return px, py


def render_ims(png, lat, lon, when, background=None):
    """Display frame from an IMS radar overlay, CROP_KM around (lat, lon)."""
    img = Image.open(BytesIO(png)).convert('RGBA')
    w, h = img.size
    west, south, east, north = IMS_BOUNDS
    px, py = ims_pixel(lat, lon, w, h)
    km_per_unit = 6378.137 * math.cos(math.radians(lat))  # ground km per radian / Mercator unit at lat
    hx = CROP_KM / 2 / (math.radians(east - west) * km_per_unit / w)
    hy = CROP_KM / 2 / ((_merc_y(north) - _merc_y(south)) * km_per_unit / h)
    return _compose(img, tuple(round(v) for v in (px - hx, py - hy, px + hx, py + hy)), when, background)


def ims_frames_in_window(items, alert_time, window=WINDOW):
    """IMS `data.types.IMSRadar` items -> [(naive UTC, image url)] in the window, oldest first.
    `forecast_time` is ISRAEL LOCAL time (the `created` field is UTC), so convert it."""
    end = to_utc_naive(alert_time)
    start = end - window
    selected = []
    for item in items or []:
        name = item.get('file_name') or ''
        if not name.endswith('_0.png'):  # the IMS site only draws the *_0.png layers
            continue
        try:
            local = datetime.strptime(item['forecast_time'], '%Y-%m-%d %H:%M:%S')
        except (KeyError, TypeError, ValueError):
            continue
        ts = to_utc_naive(local.replace(tzinfo=ISRAEL_TZ))
        if start <= ts <= end:
            selected.append((ts, IMS_BASE + name))
    return sorted(selected)


def _remaining(deadline):
    return deadline - time.monotonic()


def _timeout(deadline):
    return max(1.0, min(HTTP_TIMEOUT, _remaining(deadline)))


def _result(future, deadline):
    """A future's result if it finishes within the deadline, else None."""
    if future is None:
        return None
    wait([future], timeout=max(0.0, _remaining(deadline)))
    return future.result() if future.done() and not future.exception() else None


def _get_bytes(url, timeout):
    resp = requests.get(url, timeout=timeout, headers={'User-Agent': BROWSER_UA})
    resp.raise_for_status()
    return resp.content


def current_thresholds():
    """Upper-case numeric/list constants of the detector, as a fallback when the
    diagnostics do not carry their own thresholds."""
    try:
        from app.radar_global import GlobalRadarService
        return {k: v for k, v in vars(GlobalRadarService).items()
                if k.isupper() and isinstance(v, (int, float, list, tuple))}
    except Exception as e:
        return {'error': str(e)}


# ---------- snapshot ----------

def _rainviewer_jobs(lat, lon, alert_time, deadline):
    """[(source, utc, kind, url)] for the RainViewer frames in the window (API keeps ~2 h)."""
    api = requests.get(RadarService.API_URL, timeout=_timeout(deadline)).json()
    host = api.get('host', 'https://tilecache.rainviewer.com')
    jobs = []
    for ts, frame in frames_in_window(api.get('radar', {}).get('past', []), alert_time):
        for kind, scheme in (('display', DISPLAY_SCHEME), ('raw', RAW_SCHEME)):
            url = f"{host}{frame['path']}/{TILE_SIZE}/{ZOOM}/{lat:.4f}/{lon:.4f}/{scheme}.png"
            jobs.append(('rainviewer', ts, kind, url))
    return jobs


def _ims_jobs(alert_time, deadline):
    """[(source, utc, kind, url)] for the IMS radar frames in the window (~2 h listed, 5-min steps)."""
    resp = requests.get(IMS_API, timeout=_timeout(deadline), headers={'User-Agent': BROWSER_UA})
    resp.raise_for_status()
    items = resp.json()['data']['types'].get('IMSRadar') or []
    return [('ims', ts, 'raw', url) for ts, url in ims_frames_in_window(items, alert_time)]


def _download(pool, jobs, deadline, errors):
    """{(source, utc, kind): bytes} for the jobs that finished in time and look valid."""
    futures = {pool.submit(_get_bytes, url, _timeout(deadline)): (source, ts, kind)
               for source, ts, kind, url in jobs}
    done, not_done = wait(futures, timeout=max(0.0, _remaining(deadline)))
    if not_done:
        errors.append(f'{len(not_done)} downloads exceeded the time budget')
    contents = {}
    for future in done:
        source, ts, kind = futures[future]
        label = f'{source} {kind} {utc_stamp(ts)}'
        try:
            content = future.result()
        except Exception as e:
            errors.append(f'{label}: {e}')
            continue
        # RainViewer must be RGBA (z>7 returns a palette placeholder); IMS overlays are palette PNGs
        ok = is_valid_tile(content) if source == 'rainviewer' else content[:4] == b'\x89PNG'
        if ok:
            contents[(source, ts, kind)] = content
        else:
            errors.append(f'{label}: not a valid radar PNG')
    return contents


def _write_frames(contents, lat, lon, alert_dir, background, errors):
    """Write rv_* and ims_* files. Returns {source: [{time, display, raw}]}, oldest first."""
    written = {'rainviewer': [], 'ims': []}
    prefixes = {'rainviewer': 'rv', 'ims': 'ims'}
    for source, ts in sorted({(s, t) for s, t, _ in contents}):
        prefix, stamp = prefixes[source], utc_stamp(ts)
        entry = {'time': iso_utc(ts)}
        try:
            raw = contents.get((source, ts, 'raw'))
            if raw:
                entry['raw'] = f"{prefix}_raw_{stamp}.png"
                with open(os.path.join(alert_dir, entry['raw']), 'wb') as f:
                    f.write(raw)
            img = None
            if source == 'rainviewer' and (source, ts, 'display') in contents:
                img = render_frame(contents[(source, ts, 'display')], lat, lon, ts, background=background)
            elif source == 'ims' and raw:
                img = render_ims(raw, lat, lon, ts, background=background)
            if img is not None:
                entry['display'] = f"{prefix}_{stamp}.png"
                save_display(img, os.path.join(alert_dir, entry['display']))
        except Exception as e:
            errors.append(f'{source} save {stamp}: {e}')
        if 'display' in entry or 'raw' in entry:
            written[source].append(entry)
    return written


def _save_buffered_rainviewer(lat, lon, alert_time, alert_dir, errors, background=None):
    """Fallback when the live RainViewer fetch failed: crop the Israel-centred buffer frames."""
    saved = []
    for ts, path in RadarService.buffered_images(RadarService.get_radar_directory(), 'radar_',
                                                 alert_time - WINDOW, alert_time):
        try:
            with open(path, 'rb') as f:
                img = render_frame(f.read(), lat, lon, ts,
                                   center=(RadarService.ISRAEL_LAT, RadarService.ISRAEL_LON),
                                   background=background)
            name = f"rv_{utc_stamp(ts)}.png"
            save_display(img, os.path.join(alert_dir, name))
            saved.append({'time': iso_utc(ts), 'source': 'buffer', 'display': name})
        except Exception as e:
            errors.append(f'buffer frame {os.path.basename(path)}: {e}')
    return saved


def _save_weather2day(result, alert_time, alert_dir, errors):
    """Save the latest weather2day image (not georeferenced, so no crop or marker)."""
    if not result:
        errors.append('weather2day: no image')
        return []
    ts, content = result
    if ts < alert_time - WINDOW:
        errors.append(f'weather2day: latest image is stale ({iso_utc(ts)})')
        return []
    name = f"w2d_{utc_stamp(ts)}.png"
    with open(os.path.join(alert_dir, name), 'wb') as f:
        f.write(content)
    return [{'time': iso_utc(ts), 'file': name}]


def _alert_row(alert):
    keys = ('id', 'location_id', 'alert_time', 'rain_expected_at', 'minutes_ahead', 'message',
            'dismissed', 'created_at', 'user_feedback')
    return {k: getattr(alert, k, None) for k in keys}


def _location_row(location):
    if location is None:
        return None
    return {k: getattr(location, k, None) for k in ('id', 'address', 'latitude', 'longitude')}


def snapshot_alert(alert, diagnostics=None, alerts_dir=None, budget_s=TIME_BUDGET_S):
    """
    Save the data behind an alert to <alerts_dir>/<alert.id>/ (all names are UTC):
      ims_<UTC>.png       IMS radar (5-min steps), CROP_KM around the location, with marker
      ims_raw_<UTC>.png   original IMS overlay (940x940, georeferenced by IMS_BOUNDS)
      rv_<UTC>.png        RainViewer display frames (scheme 2), CROP_KM around the location
      rv_raw_<UTC>.png    RainViewer 512px tiles (scheme 0, dBZ-decodable), ~265 km across, centred
      w2d_<UTC>.png       latest weather2day radar image (not georeferenced)
      preview.png         latest display frame, IMS preferred (for notifications)
      detection.json      diagnostics, thresholds, location, alert row, file list, errors
    Call after the alert is committed. Bounded by budget_s, never raises.
    Sets alert.radar_images_saved to the dir relative to data/ and prunes data/alerts.
    Returns the alert dir path, or None if it could not be created.
    """
    deadline = time.monotonic() + budget_s
    root = alerts_dir or ALERTS_DIR
    alert_id = getattr(alert, 'id', None)
    alert_dir = os.path.join(root, str(alert_id))
    alert_time = to_utc_naive(getattr(alert, 'created_at', None)) or utc_now()
    location = getattr(alert, 'location', None)
    errors, preview, w2d_files = [], None, []
    frames = {'rainviewer': [], 'ims': []}

    try:
        os.makedirs(alert_dir, exist_ok=True)
    except Exception as e:
        print(f"[Capture] Cannot create {alert_dir}: {e}")
        return None

    try:
        lat, lon = location.latitude, location.longitude
        pool = ThreadPoolExecutor(max_workers=8)
        try:
            w2d_future = pool.submit(RadarService.fetch_weather2day, HTTP_TIMEOUT)
            bg_future = pool.submit(basemap, lat, lon, deadline)
            listings = {'rainviewer': pool.submit(_rainviewer_jobs, lat, lon, alert_time, deadline)}
            if in_ims_coverage(lat, lon):
                listings['ims'] = pool.submit(_ims_jobs, alert_time, deadline)
            jobs = []
            for name, future in listings.items():
                wait([future], timeout=max(0.0, _remaining(deadline)))
                try:
                    found = future.result(timeout=0)
                except Exception as e:
                    errors.append(f'{name} frame list: {e!r}')
                    continue
                jobs += found
                if not found:
                    errors.append(f'{name}: no frames in window')
            contents = _download(pool, jobs, deadline, errors)
            background = _result(bg_future, deadline)
            w2d = _result(w2d_future, deadline)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        frames = _write_frames(contents, lat, lon, alert_dir, background, errors)
        if not frames['rainviewer']:
            frames['rainviewer'] = _save_buffered_rainviewer(lat, lon, alert_time, alert_dir, errors, background)
        w2d_files = _save_weather2day(w2d, alert_time, alert_dir, errors)

        displays = ([f['display'] for f in frames['ims'] if 'display' in f]
                    or [f['display'] for f in frames['rainviewer'] if 'display' in f]
                    or [f['file'] for f in w2d_files])  # w2d has no marker: not georeferenced here
        if displays:
            shutil.copyfile(os.path.join(alert_dir, displays[-1]), os.path.join(alert_dir, 'preview.png'))
            preview = 'preview.png'
    except Exception as e:
        errors.append(f'snapshot: {e!r}')

    try:
        diag = diagnostics if isinstance(diagnostics, dict) else {}
        rv_km_per_px = round(km_per_px(location.latitude), 4) if location else None
        doc = {
            'schema_version': 1,
            'captured_at': utc_now(),
            'window_utc': {'start': alert_time - WINDOW, 'end': alert_time},
            'alert': _alert_row(alert),
            'location': _location_row(location),
            'diagnostics': diagnostics,
            'thresholds': diag.get('thresholds') or current_thresholds(),
            'render': {'crop_km': CROP_KM, 'out_px': OUT_PX, 'rings_km': RINGS_KM,
                       'rainviewer': {'zoom': ZOOM, 'tile_size': TILE_SIZE, 'display_scheme': DISPLAY_SCHEME,
                                      'raw_scheme': RAW_SCHEME, 'raw_km_per_px': rv_km_per_px},
                       'ims': {'bounds_wsen': IMS_BOUNDS, 'projection': 'linear stretch in Web Mercator'}},
            'files': {'ims': frames['ims'], 'rainviewer': frames['rainviewer'], 'weather2day': w2d_files,
                      'preview': preview},
            'errors': errors,
        }
        with open(os.path.join(alert_dir, 'detection.json'), 'w', encoding='utf-8') as f:
            f.write(_dumps(doc, ensure_ascii=False, indent=1))
    except Exception as e:
        print(f"[Capture] Error writing detection.json for alert {alert_id}: {e}")

    print(f"[Capture] Alert {alert_id}: {len(frames['ims'])} IMS frames, {len(frames['rainviewer'])} "
          f"RainViewer frames, {len(w2d_files)} weather2day, {len(errors)} issues, "
          f"{time.monotonic() - deadline + budget_s:.1f}s")
    for err in errors:
        print(f"[Capture]   {err}")

    try:
        alert.radar_images_saved = os.path.relpath(alert_dir, DATA_DIR)
        db.session.commit()
    except Exception as e:
        print(f"[Capture] Could not record capture dir on alert {alert_id}: {e}")
        with contextlib.suppress(Exception):
            db.session.rollback()

    try:
        prune(alerts_dir=root, keep={alert_id})
    except Exception as e:
        print(f"[Capture] Prune failed: {e}")
    return alert_dir


def preview_path(alert_dir):
    """Path of preview.png in an alert dir, or None (for notifications)."""
    path = os.path.join(alert_dir, 'preview.png') if alert_dir else None
    return path if path and os.path.exists(path) else None


def _hhmm(filename):
    ts = parse_stamp(filename)
    return ts.strftime('%H:%M') if ts else '?'


def load_capture(alert_id, alerts_dir=None):
    """What the review page needs from an alert dir, or None if nothing was captured."""
    alert_dir = os.path.join(alerts_dir or ALERTS_DIR, str(alert_id))
    if not os.path.isdir(alert_dir):
        return None
    files = sorted(os.listdir(alert_dir))

    def pick(prefix):
        return [(f, _hhmm(f)) for f in files if f.startswith(prefix) and '_raw_' not in f]

    strips = [('IMS radar', pick('ims_')), ('RainViewer', pick('rv_')), ('weather2day', pick('w2d_'))]
    info = {
        'strips': [(title, items) for title, items in strips if items],
        'anim': strips[0][1] or strips[1][1],  # animate IMS (5-min steps) when available
        'raw_count': sum(1 for f in files if '_raw_' in f),
        'preview': 'preview.png' if 'preview.png' in files else None,
        'size_kb': round(dir_size(alert_dir) / 1024),
        'summary': {}, 'thresholds': {}, 'errors': [],
        'has_json': 'detection.json' in files,
    }
    if info['has_json']:
        try:
            with open(os.path.join(alert_dir, 'detection.json'), encoding='utf-8') as f:
                doc = json.load(f)
            diag = doc.get('diagnostics') or {}
            keys = ('should_alert', 'reason', 'source', 'current_distance_km', 'max_dbz', 'intensity',
                    'velocity_kmh', 'minutes_until_rain', 'expected_at', 'direction', 'bearing_deg',
                    'approaching', 'confidence')
            info['summary'] = {k: diag[k] for k in keys if k in diag}
            info['thresholds'] = doc.get('thresholds') or {}
            info['errors'] = doc.get('errors') or []
        except Exception as e:
            info['errors'] = [f'detection.json unreadable: {e}']
    return info


# ---------- storage bounds ----------

def dir_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(dirpath, name))
    return total


def _labelled_alert_ids():
    return {row[0] for row in db.session.query(Alert.id).filter(Alert.user_feedback.isnot(None))}


def prune(max_bytes=MAX_ALERTS_BYTES, alerts_dir=None, labelled_ids=None, keep=()):
    """
    Keep data/alerts under max_bytes. Deletes the oldest (lowest id) unlabelled alert dirs
    first (user_feedback IS NULL, or no alert row), then the oldest labelled ones.
    `labelled_ids` defaults to a DB query (needs an app context). Returns removed alert ids.
    """
    root = alerts_dir or ALERTS_DIR
    if not os.path.isdir(root):
        return []
    dirs = {int(name): os.path.join(root, name) for name in os.listdir(root)
            if name.isdigit() and os.path.isdir(os.path.join(root, name))}
    sizes = {aid: dir_size(path) for aid, path in dirs.items()}
    total = sum(sizes.values())
    if total <= max_bytes:
        return []

    if labelled_ids is None:
        labelled_ids = _labelled_alert_ids()
    removed = []
    for aid in sorted(dirs, key=lambda a: (a in labelled_ids, a)):
        if total <= max_bytes:
            break
        if aid in keep:
            continue
        shutil.rmtree(dirs[aid], ignore_errors=True)
        total -= sizes[aid]
        removed.append(aid)
    print(f"[Capture] Pruned {len(removed)} alert dirs {removed}; data/alerts now {total / 1e6:.1f} MB")
    return removed


def prune_checks(days=CHECK_RETENTION_DAYS):
    """Delete detection_checks rows older than `days`. Returns the number of rows deleted."""
    cutoff = utc_now() - timedelta(days=days)
    deleted = DetectionCheck.query.filter(DetectionCheck.checked_at < cutoff).delete(synchronize_session=False)
    db.session.commit()
    return deleted


def maintenance_if_due(now=None):
    """Daily prune of data/alerts and detection_checks. Call from a scheduler job inside an
    app context; runs on the first call after start-up, then every 24 h. Never raises."""
    global _last_maintenance
    now = to_utc_naive(now) or utc_now()
    if _last_maintenance and now - _last_maintenance < MAINTENANCE_INTERVAL:
        return False
    _last_maintenance = now
    try:
        prune()
    except Exception as e:
        print(f"[Capture] Daily prune failed: {e}")
    try:
        deleted = prune_checks()
        if deleted:
            print(f"[Capture] Deleted {deleted} detection_checks rows older than {CHECK_RETENTION_DAYS} days")
    except Exception as e:
        print(f"[Capture] detection_checks prune failed: {e}")
        db.session.rollback()
    return True


# ---------- per-check log ----------

def _first_number(d, *keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, numbers.Number) and not isinstance(v, bool):
            return float(v)
    return None


def _compact_value(v):
    if isinstance(v, datetime):
        return iso_utc(v)
    if isinstance(v, str):
        return v[:120]
    if isinstance(v, numbers.Number) and not isinstance(v, (bool, int)):
        f = float(v)
        return round(f, 3) if math.isfinite(f) else None
    if hasattr(v, 'item'):
        return v.item()
    return v


def _is_scalar(v):
    return v is None or isinstance(v, (bool, str, datetime, numbers.Number))


def compact_details(diagnostics, max_chars=DETAILS_MAX_CHARS):
    """Scalar diagnostics (plus a per-frame scalar summary if it fits) as JSON <= max_chars."""
    if not diagnostics:
        return None
    out = {k: _compact_value(v) for k, v in diagnostics.items() if _is_scalar(v)}
    frames = diagnostics.get('frames')
    if isinstance(frames, list):
        out['frames_n'] = len(frames)
        summary = [{k: _compact_value(v) for k, v in f.items() if _is_scalar(v)}
                   for f in frames if isinstance(f, dict)]
        with_frames = dict(out, frames=summary)
        if len(_dumps(with_frames, separators=(',', ':'))) <= max_chars:
            out = with_frames
    text = _dumps(out, separators=(',', ':'))
    for key in sorted(out, key=lambda k: len(_dumps(out[k])), reverse=True):
        if len(text) <= max_chars:
            break
        del out[key]
        text = _dumps(out, separators=(',', ':'))
    return text


def log_check(location, diagnostics, checked_at=None):
    """Record one detection_checks row. `diagnostics` may be None (no rain found), the legacy
    check_rain_at_location() dict, or analyze_location() diagnostics. Never raises."""
    try:
        d = diagnostics if isinstance(diagnostics, dict) else {}
        default_reason = 'no rain' if diagnostics is None else 'rain detected'
        row = DetectionCheck(
            checked_at=to_utc_naive(checked_at) or utc_now(),
            location_id=location.id,
            should_alert=bool(d.get('should_alert', diagnostics is not None)),
            reason=str(d.get('reason') or default_reason)[:200],
            distance_km=_first_number(d, 'current_distance_km', 'nearest_distance_km', 'distance_km'),
            max_dbz=_first_number(d, 'max_dbz'),
            intensity=_first_number(d, 'intensity'),
            velocity_kmh=_first_number(d, 'velocity_kmh'),
            eta_minutes=_first_number(d, 'minutes_until_rain', 'eta_minutes'),
            details=compact_details(diagnostics),
        )
        db.session.add(row)
        db.session.commit()
        return row
    except Exception as e:
        print(f"[Capture] log_check failed for location {getattr(location, 'id', None)}: {e}")
        with contextlib.suppress(Exception):
            db.session.rollback()
        return None
