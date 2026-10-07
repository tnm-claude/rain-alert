"""
Radar-based rain nowcasting using RainViewer's free tile API.

Free API facts (verified 2026-10): past frames only (2 h, every 10 min), no
nowcast, max zoom 7 (z8+ returns a "Zoom Level Not Supported" image with
HTTP 200), only colour scheme 2 "Universal Blue" (other scheme ids silently
return the same image), 100 requests/IP/minute. Israel is covered by the IMS
Beit Dagan (ILBG) and Mekorot Dalton (ILDL) radars.

Per check: 1 API request, then each frame's tiles around the location are
fetched once (2 tiles for Ramat Gan) and cached in memory, so steady state
is ~3 requests per check. All analysis lives in app.detection.
"""
import math
import time
from collections import OrderedDict
from io import BytesIO
from typing import ClassVar

import numpy as np
import requests
from PIL import Image

from app import detection


class RadarFetchError(Exception):
    pass


class GlobalRadarService:
    """Detect rain at / approaching a location from RainViewer radar frames"""

    API_URL = "https://api.rainviewer.com/public/weather-maps.json"
    SOURCE = "rainviewer"
    ZOOM_LEVEL = 7          # max zoom on the free API
    TILE_SIZE = 256         # 256 px at z7 = ~1.04 km/px at 32N
    COLOR_SCHEME = 2        # Universal Blue (the only scheme served)
    TILE_OPTIONS = "0_0"    # smoothing off (exact palette colours), snow not separated
    HTTP_TIMEOUT = 8        # seconds per request
    TIME_BUDGET_S = 60      # stop fetching older frames after this long
    MAX_CONSECUTIVE_FAILURES = 3
    MAX_UNKNOWN_PIXEL_FRACTION = 0.01  # more unknown colours than this => not a radar tile
    CACHE_TILES = 128

    _tile_cache: ClassVar[OrderedDict] = OrderedDict()

    @staticmethod
    def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple:
        """Convert lat/lon to tile coordinates at given zoom level"""
        x, y = detection.lat_lon_to_global_px(lat, lon, zoom, 1)
        return (int(x), int(y))

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Distance between two points in kilometers"""
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        return 6371 * 2 * math.asin(math.sqrt(a))

    # ------------------------------------------------------------------ I/O
    @staticmethod
    def fetch_frame_list() -> tuple:
        """Returns (host, [{'time': epoch, 'path': str}, ...] oldest first)."""
        response = requests.get(GlobalRadarService.API_URL, timeout=GlobalRadarService.HTTP_TIMEOUT)
        if response.status_code != 200:
            raise RadarFetchError(f"API returned HTTP {response.status_code}")
        data = response.json()
        frames = [f for f in data.get('radar', {}).get('past', []) if f.get('time') and f.get('path')]
        return data.get('host', 'https://tilecache.rainviewer.com'), sorted(frames, key=lambda f: f['time'])

    @staticmethod
    def tile_url(host: str, path: str, x: int, y: int) -> str:
        s = GlobalRadarService
        return f"{host}{path}/{s.TILE_SIZE}/{s.ZOOM_LEVEL}/{x}/{y}/{s.COLOR_SCHEME}/{s.TILE_OPTIONS}.png"

    @staticmethod
    def fetch_tile_dbz(host: str, path: str, x: int, y: int, stats: dict) -> np.ndarray:
        """Decoded dBZ array for one tile (cached; frames never change once published)."""
        s = GlobalRadarService
        key = (path, s.ZOOM_LEVEL, s.TILE_SIZE, x, y)
        if key in s._tile_cache:
            s._tile_cache.move_to_end(key)
            return s._tile_cache[key]
        stats['http_requests'] += 1
        response = requests.get(s.tile_url(host, path, x, y), timeout=s.HTTP_TIMEOUT)
        if response.status_code != 200:
            raise RadarFetchError(f"tile {x}/{y} HTTP {response.status_code}")
        img = Image.open(BytesIO(response.content)).convert('RGBA')
        if img.size != (s.TILE_SIZE, s.TILE_SIZE):
            raise RadarFetchError(f"tile {x}/{y} unexpected size {img.size}")
        dbz, unknown = detection.decode_rgba(np.asarray(img))
        if unknown > s.MAX_UNKNOWN_PIXEL_FRACTION * dbz.size:
            # e.g. the "Zoom Level Not Supported" placeholder, served with HTTP 200
            raise RadarFetchError(f"tile {x}/{y} is not radar data ({unknown} unknown colours)")
        s._tile_cache[key] = dbz
        while len(s._tile_cache) > s.CACHE_TILES:
            s._tile_cache.popitem(last=False)
        return dbz

    @staticmethod
    def mosaic_window(lat: float, lon: float) -> tuple:
        """Global-pixel window (gx0, gy0, height, width) covering ANALYSIS_RADIUS_KM around the location."""
        s = GlobalRadarService
        cx, cy = detection.lat_lon_to_global_px(lat, lon, s.ZOOM_LEVEL, s.TILE_SIZE)
        km_per_px = 40075.016 * math.cos(math.radians(lat)) / ((2 ** s.ZOOM_LEVEL) * s.TILE_SIZE)
        r = math.ceil(detection.ANALYSIS_RADIUS_KM / km_per_px) + 2
        gx0, gy0 = int(cx) - r, int(cy) - r
        return gx0, gy0, 2 * r + 1, 2 * r + 1

    @staticmethod
    def fetch_mosaic(host: str, path: str, window: tuple, stats: dict) -> np.ndarray:
        """Assemble the dBZ window from the (few) tiles it overlaps."""
        s = GlobalRadarService
        gx0, gy0, h, w = window
        out = np.full((h, w), np.nan, dtype=np.float32)
        ts = s.TILE_SIZE
        for ty in range(gy0 // ts, (gy0 + h - 1) // ts + 1):
            for tx in range(gx0 // ts, (gx0 + w - 1) // ts + 1):
                tile = s.fetch_tile_dbz(host, path, tx, ty, stats)
                # overlap of this tile with the window, in global pixels
                x0, x1 = max(gx0, tx * ts), min(gx0 + w, (tx + 1) * ts)
                y0, y1 = max(gy0, ty * ts), min(gy0 + h, (ty + 1) * ts)
                out[y0 - gy0:y1 - gy0, x0 - gx0:x1 - gx0] = tile[y0 - ty * ts:y1 - ty * ts, x0 - tx * ts:x1 - tx * ts]
        return out

    # ------------------------------------------------------------- analysis
    @staticmethod
    def analyze_location(lat: float, lon: float) -> dict:
        """
        Full analysis. Never raises; always returns diagnostics including
        should_alert, reason, per-frame stats and the contract fields.
        """
        s = GlobalRadarService
        started = time.monotonic()
        stats = {'http_requests': 0}
        base = {'source': s.SOURCE, 'lat': lat, 'lon': lon, 'checked_at': detection.utcnow().isoformat(),
                'zoom': s.ZOOM_LEVEL, 'tile_size': s.TILE_SIZE, 'thresholds': dict(detection.THRESHOLDS),
                'frames': [], 'errors': []}
        try:
            stats['http_requests'] += 1
            host, frame_list = s.fetch_frame_list()
            if not frame_list:
                raise RadarFetchError("API returned no radar frames")

            window = s.mosaic_window(lat, lon)
            dist, bearing = detection.distance_bearing_grid(lat, lon, s.ZOOM_LEVEL, s.TILE_SIZE, *window)

            # Fetch newest first so a slow/failing server still yields the most recent data.
            grids = {}
            failures = 0
            for frame in reversed(frame_list):
                if failures >= s.MAX_CONSECUTIVE_FAILURES or time.monotonic() - started > s.TIME_BUDGET_S:
                    grids[frame['time']] = 'skipped (time budget / repeated failures)'
                    continue
                try:
                    grids[frame['time']] = s.fetch_mosaic(host, frame['path'], window, stats)
                    failures = 0
                except Exception as e:  # noqa: BLE001 - network, decode, placeholder tile...
                    failures += 1
                    grids[frame['time']] = f"{type(e).__name__}: {e}"
                    base['errors'].append(f"frame {frame['time']}: {e}")

            arrays = [g for g in grids.values() if isinstance(g, np.ndarray)]
            clutter = detection.clutter_mask(arrays)
            frames = []
            for frame in frame_list:
                g = grids[frame['time']]
                entry = {'epoch': frame['time'], 'time': detection.epoch_to_utc(frame['time']).isoformat()}
                if isinstance(g, np.ndarray):
                    entry.update(detection.frame_stats(g, dist, bearing, clutter))
                else:
                    entry['error'] = g
                frames.append(entry)

            result = detection.decide(frames)
            result.update(base)
            result['frames'] = frames
            result['clutter_px'] = int(clutter.sum()) if clutter is not None else None
        except Exception as e:  # noqa: BLE001 - a radar failure must never crash the job
            result = detection.decide([])
            result.update(base)
            result['reason'] = f"radar check failed: {type(e).__name__}: {e}"
            result['errors'].append(result['reason'])
        result['http_requests'] = stats['http_requests']
        result['elapsed_s'] = round(time.monotonic() - started, 2)
        s._log(result)
        return result

    @staticmethod
    def _log(result: dict):
        frames = [f for f in result.get('frames', []) if not f.get('error')]
        trend = ' '.join('-' if f['nearest_km'] is None else f"{f['nearest_km']:.0f}" for f in frames[-6:])
        verdict = 'ALERT' if result['should_alert'] else 'no alert'
        print(f"[GlobalRadar] ({result['lat']:.4f}, {result['lon']:.4f}) {verdict}: {result['reason']} | "
              f"nearest km (old->new): [{trend}] | max {result.get('max_dbz')} dBZ | "
              f"{result['http_requests']} req, {result['elapsed_s']}s")
        for err in result.get('errors', [])[:3]:
            print(f"[GlobalRadar]   error: {err}")

    @staticmethod
    def check_rain_at_location(lat: float, lon: float) -> dict | None:
        """
        Backward-compatible wrapper: the diagnostics dict when an alert is
        warranted (rain at the location or approaching within the ETA window),
        else None. Keys: minutes_until_rain, expected_at (naive UTC), intensity
        (dBZ), confidence, current_distance_km, approaching, velocity_kmh,
        bearing_deg, direction, max_dbz, frames, source (+ diagnostics).
        """
        result = GlobalRadarService.analyze_location(lat, lon)
        return result if result['should_alert'] else None
