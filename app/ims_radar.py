"""
Israel Meteorological Service (IMS) radar frames.

The old ims.gov.il/he/radar page and IMSRadar.gif are gone. The current IMS
site (https://ims.gov.il/he/radarSatellite) is driven by a JSON endpoint that
lists georeferenced, transparent radar overlay PNGs (5-minute steps, ~2 hours):

    https://ims.gov.il/he/radar_satellite/1   ->  data.types.IMSRadar[]
        {forecast_time: "2026-10-07 13:30:00" (Israel local time),
         file_name: "/sites/default/files/ims_data/map_images/IMSRadar4GIS/IMSRadar4GIS_202610071330_0.png"}

The JSON has no CORS headers, so the UI reads it through this module.
"""
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

IMS_BASE = "https://ims.gov.il"
IMS_API = IMS_BASE + "/he/radar_satellite/1"
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Overlay image extents (from the IMS site's own Leaflet code), degrees.
# Image is a linear stretch in Web Mercator, which is what OpenLayers/Leaflet do.
SOURCES = {
    "IMSRadar": {
        "dir": "IMSRadar4GIS",
        "file_re": re.compile(r"_0\.png$"),  # IMS itself only draws *_0.png
        "bounds": {"west": 31.7662932, "south": 29.4468657, "east": 37.8648132, "north": 34.5318966},
    },
    # Fallback IMS composite, used when the main IMS rain radar is switched off.
    "radar": {
        "dir": "radarComposite",
        "file_re": re.compile(r"\.gif$"),
        "bounds": {"west": 31.9347389, "south": 29.4373463, "east": 37.8574240, "north": 34.5078464},
    },
}

_FRAMES_TTL = 60  # seconds
_IMAGE_CACHE_MAX = 40
_name_re = re.compile(r"^[A-Za-z0-9_]+\.(png|gif)$")

_lock = threading.Lock()
_frames_cache = {"at": 0.0, "data": None}
_image_cache = {}  # (dir, name) -> (bytes, content_type)


class IMSRadarService:
    """Fetch IMS radar frame list and images (short in-memory cache)."""

    @staticmethod
    def _parse_time(forecast_time):
        """'2026-10-07 13:30:00' (Israel local) -> epoch seconds (UTC)."""
        local = datetime.strptime(forecast_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ISRAEL_TZ)
        return int(local.timestamp())

    @staticmethod
    def get_frames(force=False):
        """
        Return {"source", "bounds", "frames": [{"time", "file", "url"}]}, oldest first,
        or None when IMS is unreachable / has no frames. `time` is epoch seconds UTC.
        """
        with _lock:
            fresh = time.time() - _frames_cache["at"] < _FRAMES_TTL
            if fresh and not force and _frames_cache["data"]:
                return _frames_cache["data"]

        try:
            resp = requests.get(IMS_API, timeout=10, headers={"User-Agent": "Mozilla/5.0 rain-alert"})
            resp.raise_for_status()
            types = resp.json()["data"]["types"]
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"[IMS] Frame list failed: {e}")
            with _lock:
                return _frames_cache["data"]  # stale beats nothing

        result = None
        for source, cfg in SOURCES.items():
            frames = []
            for item in types.get(source) or []:
                name = item["file_name"].rsplit("/", 1)[-1]
                if not cfg["file_re"].search(name) or not _name_re.match(name):
                    continue
                try:
                    t = IMSRadarService._parse_time(item["forecast_time"])
                except (KeyError, ValueError):
                    continue
                if t > time.time() + 300:  # skip forecast frames
                    continue
                frames.append({"time": t, "file": name, "url": f"/api/radar/ims/image/{cfg['dir']}/{name}"})
            if frames:
                frames.sort(key=lambda f: f["time"])
                result = {"source": source, "bounds": cfg["bounds"], "frames": frames[-24:]}
                break

        with _lock:
            _frames_cache.update(at=time.time(), data=result)
        return result

    @staticmethod
    def get_image(directory, name):
        """Return (bytes, content_type) for an IMS overlay image, or None."""
        if directory not in {c["dir"] for c in SOURCES.values()} or not _name_re.match(name):
            return None
        key = (directory, name)
        with _lock:
            if key in _image_cache:
                return _image_cache[key]
        try:
            url = f"{IMS_BASE}/sites/default/files/ims_data/map_images/{directory}/{name}"
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 rain-alert"})
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[IMS] Image {name} failed: {e}")
            return None
        ctype = resp.headers.get("Content-Type", "image/png")
        if not ctype.startswith("image/"):
            return None
        with _lock:
            if len(_image_cache) >= _IMAGE_CACHE_MAX:
                _image_cache.pop(next(iter(_image_cache)))
            _image_cache[key] = (resp.content, ctype)
        return resp.content, ctype
