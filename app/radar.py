"""
Rolling RainViewer radar buffer (fallback for alert capture) and the weather2day fetch.

Buffered files are named by their UTC time (bug #2: names used to be local time
while alerts are stored in UTC, so alert-time lookups never matched).
"""
import os
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

STAMP_FORMAT = "%Y%m%dT%H%MZ"  # UTC, e.g. 20261007T1030Z
_STAMP_RE = re.compile(r"(\d{8}T\d{4}Z)")


def utc_now():
    """Naive UTC now (matches Alert.created_at = datetime.utcnow())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(dt):
    """Normalise a datetime to naive UTC (naive input is assumed to be UTC already)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def utc_from_unix(ts):
    """Unix timestamp -> naive UTC datetime (never local time)."""
    return datetime.fromtimestamp(int(ts), timezone.utc).replace(tzinfo=None)


def utc_stamp(dt):
    return to_utc_naive(dt).strftime(STAMP_FORMAT)


def parse_stamp(filename):
    """Return the naive UTC datetime embedded in a filename, or None."""
    m = _STAMP_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), STAMP_FORMAT)
    except ValueError:
        return None


def is_valid_tile(content):
    """RainViewer tiles are RGBA PNGs. Unsupported zooms return HTTP 200 with a
    palette ('P') 'Zoom Level Not Supported' PNG, so check the mode, not just the magic."""
    if not content or content[:4] != b'\x89PNG':
        return False
    try:
        return Image.open(BytesIO(content)).mode == 'RGBA'
    except Exception:
        return False


class RadarService:
    """Rolling RainViewer buffer bounded by time, plus the weather2day fetch"""

    API_URL = "https://api.rainviewer.com/public/weather-maps.json"
    WEATHER2DAY_URL = "https://www.weather2day.co.il/radar.php"
    BUFFER_MAX_AGE = timedelta(hours=2)
    HTTP_TIMEOUT = 10

    # RainViewer coordinate tile centred on Israel. size=512 is a hi-dpi tile:
    # same area as the 256 tile (~265 km across at z7), ~0.52 km/px. z7 is the max zoom.
    ISRAEL_LAT = 31.5
    ISRAEL_LON = 34.8
    ZOOM_LEVEL = 7
    TILE_SIZE = 512

    @staticmethod
    def get_radar_directory():
        """RainViewer buffer: data/radar/radar_<UTC stamp>.png"""
        basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        radar_dir = os.path.join(basedir, 'data', 'radar')
        Path(radar_dir).mkdir(parents=True, exist_ok=True)
        return radar_dir

    @staticmethod
    def fetch_all_radar_images():
        """
        Fetch all RainViewer past frames (~2 h) centred on Israel into the buffer
        Returns: number of buffered frames now available
        """
        try:
            response = requests.get(RadarService.API_URL, timeout=RadarService.HTTP_TIMEOUT)
            if response.status_code != 200:
                print(f"[Radar] API returned status {response.status_code}")
                return 0

            data = response.json()
            radar_frames = data.get('radar', {}).get('past', [])
            if not radar_frames:
                print("[Radar] No radar frames available from API")
                return 0

            host = data.get('host', 'https://tilecache.rainviewer.com')
            radar_dir = RadarService.get_radar_directory()
            fetched_count = 0

            for frame in radar_frames:
                timestamp = frame.get('time')
                path = frame.get('path')
                if not timestamp or not path:
                    continue

                filename = f"radar_{utc_stamp(utc_from_unix(timestamp))}.png"
                filepath = os.path.join(radar_dir, filename)
                if os.path.exists(filepath):
                    fetched_count += 1
                    continue

                # Format: {host}{path}/{size}/{z}/{lat}/{lon}/{color}/{smooth}_{snow}.png
                tile_url = (f"{host}{path}/{RadarService.TILE_SIZE}/{RadarService.ZOOM_LEVEL}/"
                            f"{RadarService.ISRAEL_LAT}/{RadarService.ISRAEL_LON}/2/1_0.png")
                try:
                    tile_response = requests.get(tile_url, timeout=RadarService.HTTP_TIMEOUT)
                    if tile_response.status_code == 200 and is_valid_tile(tile_response.content):
                        with open(filepath, 'wb') as f:
                            f.write(tile_response.content)
                        fetched_count += 1
                    else:
                        print(f"[Radar] Invalid response for {filename} (HTTP {tile_response.status_code})")
                except Exception as e:
                    print(f"[Radar] Error fetching {filename}: {e}")

            print(f"[Radar] RainViewer buffer: {fetched_count}/{len(radar_frames)} frames")
            return fetched_count

        except Exception as e:
            print(f"[Radar] Error in fetch_all_radar_images: {e}")
            return 0

    @staticmethod
    def fetch_weather2day(timeout=None):
        """
        Latest weather2day radar image (~200 KB PNG, 512x512, not georeferenced, ~10-15 min updates).
        radar.php 302-redirects to pics/radar/<unix epoch>.png; the epoch is the image's
        UTC publish time (the image's own label is local time, a few minutes earlier).
        Returns (naive UTC datetime, bytes) or None. Never raises.
        """
        try:
            resp = requests.get(RadarService.WEATHER2DAY_URL,
                                timeout=timeout or RadarService.HTTP_TIMEOUT,
                                headers={'User-Agent': 'Mozilla/5.0 (rain-alert)'})
            content = resp.content
            if resp.status_code != 200 or content[:4] != b'\x89PNG' or len(content) < 10000:
                print(f"[Radar] weather2day invalid response: HTTP {resp.status_code}, "
                      f"{len(content)} bytes, url={resp.url}")
                return None
            m = re.search(r"/(\d{9,11})\.png", resp.url or '')
            return (utc_from_unix(m.group(1)) if m else utc_now()), content
        except Exception as e:
            print(f"[Radar] weather2day fetch failed: {e}")
            return None

    @staticmethod
    def cleanup_old_images(now=None):
        """Delete buffered frames older than BUFFER_MAX_AGE (by UTC name), plus legacy
        local-time names (radar_YYYYmmddHHMM.png). Returns the number of files removed."""
        cutoff = (to_utc_naive(now) or utc_now()) - RadarService.BUFFER_MAX_AGE
        radar_dir = RadarService.get_radar_directory()
        removed = 0
        try:
            for filename in os.listdir(radar_dir):
                if not (filename.startswith('radar_') and filename.endswith('.png')):
                    continue
                ts = parse_stamp(filename)
                if ts is None or ts < cutoff:
                    try:
                        os.remove(os.path.join(radar_dir, filename))
                        removed += 1
                    except OSError as e:
                        print(f"[Radar] Error removing {filename}: {e}")
        except Exception as e:
            print(f"[Radar] Error during cleanup: {e}")
        return removed

    @staticmethod
    def buffered_images(directory, prefix, start, end):
        """[(utc datetime, path)] of buffered images with start <= time <= end, oldest first"""
        start, end = to_utc_naive(start), to_utc_naive(end)
        found = []
        try:
            for filename in os.listdir(directory):
                if not (filename.startswith(prefix) and filename.endswith('.png')):
                    continue
                ts = parse_stamp(filename)
                if ts is not None and start <= ts <= end:
                    found.append((ts, os.path.join(directory, filename)))
        except FileNotFoundError:
            pass
        return sorted(found)

    @staticmethod
    def get_available_images():
        """
        Buffered RainViewer frames, oldest first
        Returns: list of dicts with filename, timestamp (ISO UTC) and display_time (server local)
        """
        images = []
        try:
            for filename in os.listdir(RadarService.get_radar_directory()):
                if not (filename.startswith('radar_') and filename.endswith('.png')):
                    continue
                ts = parse_stamp(filename)
                if ts is None:
                    continue
                images.append({
                    'filename': filename,
                    'timestamp': ts.isoformat() + 'Z',
                    'display_time': ts.replace(tzinfo=timezone.utc).astimezone().strftime('%H:%M'),
                })
        except Exception as e:
            print(f"[Radar] Error getting available images: {e}")
        images.sort(key=lambda x: x['timestamp'])
        return images
