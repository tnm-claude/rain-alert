"""
Global radar-based nowcasting service using RainViewer tile server
Now includes predictive rain detection by analyzing movement patterns
"""
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from PIL import Image
from io import BytesIO
import math


class GlobalRadarService:
    """Check rain at any global location using RainViewer's tile server with predictive capabilities"""

    API_URL = "https://api.rainviewer.com/public/weather-maps.json"
    ZOOM_LEVEL = 7  # Zoom level for radar tiles

    # Detection parameters
    CHECK_RADIUS_KM = [5, 10, 15, 20, 25]  # Concentric circles to check (in km)
    FRAMES_TO_ANALYZE = 4  # Last 4 frames (~40 minutes)
    PREDICTION_THRESHOLD_MINUTES = 30  # Alert if rain within 30 minutes (storms can move 90+ km/h)
    MIN_INTENSITY_THRESHOLD = 50  # Minimum alpha value to consider as rain

    @staticmethod
    def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple:
        """Convert lat/lon to tile coordinates at given zoom level"""
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        return (x, y)

    @staticmethod
    def lat_lon_to_pixel_in_tile(lat: float, lon: float, zoom: int, tile_size: int = 256) -> tuple:
        """Convert lat/lon to pixel coordinates within a tile"""
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        x_tile = (lon + 180.0) / 360.0 * n
        y_tile = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n

        # Get pixel within the tile
        pixel_x = int((x_tile - int(x_tile)) * tile_size)
        pixel_y = int((y_tile - int(y_tile)) * tile_size)

        return (pixel_x, pixel_y)

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in kilometers using Haversine formula"""
        R = 6371  # Earth radius in kilometers

        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        lat1 = math.radians(lat1)
        lat2 = math.radians(lat2)

        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))

        return R * c

    @staticmethod
    def get_point_at_distance(lat: float, lon: float, distance_km: float, bearing_degrees: float) -> Tuple[float, float]:
        """Get lat/lon of a point at given distance and bearing from origin"""
        R = 6371  # Earth radius in km

        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        bearing_rad = math.radians(bearing_degrees)

        lat2_rad = math.asin(
            math.sin(lat_rad) * math.cos(distance_km / R) +
            math.cos(lat_rad) * math.sin(distance_km / R) * math.cos(bearing_rad)
        )

        lon2_rad = lon_rad + math.atan2(
            math.sin(bearing_rad) * math.sin(distance_km / R) * math.cos(lat_rad),
            math.cos(distance_km / R) - math.sin(lat_rad) * math.sin(lat2_rad)
        )

        return (math.degrees(lat2_rad), math.degrees(lon2_rad))

    @staticmethod
    def check_rain_in_radius(lat: float, lon: float, radius_km: float, host: str, path: str) -> Tuple[bool, int]:
        """
        Check if there's rain within a given radius around a location
        Returns (rain_detected, max_intensity)
        """
        # Sample points around the circle (8 directions + center)
        sample_points = [(lat, lon)]  # Start with center

        for bearing in [0, 45, 90, 135, 180, 225, 270, 315]:  # 8 directions
            sample_lat, sample_lon = GlobalRadarService.get_point_at_distance(lat, lon, radius_km, bearing)
            sample_points.append((sample_lat, sample_lon))

        max_intensity = 0
        rain_detected = False

        for sample_lat, sample_lon in sample_points:
            try:
                # Calculate tile coordinates
                tile_x, tile_y = GlobalRadarService.lat_lon_to_tile(sample_lat, sample_lon, GlobalRadarService.ZOOM_LEVEL)

                # Build tile URL
                tile_url = f"{host}{path}/256/{GlobalRadarService.ZOOM_LEVEL}/{tile_x}/{tile_y}/2/1_0.png"

                # Fetch the tile (with caching in requests)
                tile_response = requests.get(tile_url, timeout=5)
                if tile_response.status_code != 200:
                    continue

                # Load image
                img = Image.open(BytesIO(tile_response.content))
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')

                # Get pixel coordinates within the tile
                pixel_x, pixel_y = GlobalRadarService.lat_lon_to_pixel_in_tile(
                    sample_lat, sample_lon, GlobalRadarService.ZOOM_LEVEL
                )

                # Check if coordinates are valid
                if not (0 <= pixel_x < img.width and 0 <= pixel_y < img.height):
                    continue

                # Check a 3x3 area around the sample point
                for dx in range(-1, 2):
                    for dy in range(-1, 2):
                        check_x = pixel_x + dx
                        check_y = pixel_y + dy

                        if not (0 <= check_x < img.width and 0 <= check_y < img.height):
                            continue

                        r, g, b, a = img.getpixel((check_x, check_y))

                        if a > GlobalRadarService.MIN_INTENSITY_THRESHOLD:
                            rain_detected = True
                            max_intensity = max(max_intensity, a)

            except Exception as e:
                # Skip this sample point on error
                continue

        return (rain_detected, max_intensity)

    @staticmethod
    def check_rain_at_location(lat: float, lon: float) -> Optional[Dict]:
        """
        Check if rain is present or approaching a location using predictive analysis
        Returns dict with rain info or None if no rain detected/predicted
        """
        try:
            # Get radar data
            response = requests.get(GlobalRadarService.API_URL, timeout=10)
            if response.status_code != 200:
                print(f"[GlobalRadar] API returned status {response.status_code}")
                return None

            data = response.json()
            radar_frames = data.get('radar', {}).get('past', [])

            if not radar_frames:
                print("[GlobalRadar] No radar frames available")
                return None

            host = data.get('host', 'https://tilecache.rainviewer.com')

            # Take the last N frames for analysis
            frames_to_check = radar_frames[-GlobalRadarService.FRAMES_TO_ANALYZE:]

            print(f"[GlobalRadar] Analyzing {len(frames_to_check)} frames for location ({lat:.4f}, {lon:.4f})")

            # Track rain distance over time
            distance_history = []  # [(timestamp, distance_km, intensity)]

            for frame in frames_to_check:
                path = frame.get('path')
                timestamp = frame.get('time')

                if not path or not timestamp:
                    continue

                # Check each radius zone
                min_distance = None
                max_intensity_at_distance = 0

                for radius in GlobalRadarService.CHECK_RADIUS_KM:
                    rain_detected, intensity = GlobalRadarService.check_rain_in_radius(
                        lat, lon, radius, host, path
                    )

                    if rain_detected:
                        min_distance = radius
                        max_intensity_at_distance = intensity
                        break  # Found rain, no need to check larger radii

                if min_distance is not None:
                    distance_history.append((timestamp, min_distance, max_intensity_at_distance))
                    time_str = datetime.fromtimestamp(timestamp).strftime('%H:%M')
                    print(f"[GlobalRadar]   Frame {time_str}: Rain at {min_distance}km, intensity={max_intensity_at_distance}")

            # Analyze movement pattern
            if len(distance_history) == 0:
                print("[GlobalRadar] No rain detected in any frame")
                return None

            # If rain is already at location (distance = 0 or very close)
            latest_distance = distance_history[-1][1]
            latest_intensity = distance_history[-1][2]

            if latest_distance <= 5:
                print(f"[GlobalRadar] ✓ Rain currently at location (distance: {latest_distance}km)")
                return {
                    'minutes_until_rain': 0,
                    'expected_at': datetime.utcnow(),
                    'intensity': latest_intensity,
                    'confidence': 'high',
                    'current_distance_km': latest_distance,
                    'approaching': True
                }

            # Check if rain is approaching OR already close
            if len(distance_history) >= 2:
                oldest_distance = distance_history[0][1]
                oldest_time = distance_history[0][0]
                latest_time = distance_history[-1][0]

                distance_change = oldest_distance - latest_distance  # Positive if approaching
                time_change_minutes = (latest_time - oldest_time) / 60.0

                # If rain is close (within 10km), ALWAYS alert regardless of direction
                if latest_distance <= 10:
                    print(f"[GlobalRadar] ✓ Rain detected nearby: {latest_distance}km away, intensity={latest_intensity}")
                    # Estimate ETA based on typical storm movement (30-60 km/h)
                    eta_minutes = int(latest_distance * 1.5)  # Assume ~40 km/h average
                    return {
                        'minutes_until_rain': eta_minutes,
                        'expected_at': datetime.utcnow() + timedelta(minutes=eta_minutes),
                        'intensity': latest_intensity,
                        'confidence': 'high' if latest_distance <= 10 else 'medium',
                        'current_distance_km': latest_distance,
                        'approaching': distance_change > 0
                    }

                if distance_change > 0 and time_change_minutes > 0:
                    # Rain is approaching!
                    velocity_km_per_min = distance_change / time_change_minutes
                    velocity_km_per_hour = velocity_km_per_min * 60

                    # Calculate ETA
                    if velocity_km_per_min > 0:
                        eta_minutes = latest_distance / velocity_km_per_min
                    else:
                        eta_minutes = 999  # Very far away

                    print(f"[GlobalRadar] ✓ Rain approaching: {latest_distance}km away, moving at {velocity_km_per_hour:.1f} km/h")
                    print(f"[GlobalRadar]   ETA: {eta_minutes:.0f} minutes")

                    # Trigger alert if rain will arrive within threshold
                    if eta_minutes <= GlobalRadarService.PREDICTION_THRESHOLD_MINUTES:
                        return {
                            'minutes_until_rain': int(eta_minutes),
                            'expected_at': datetime.utcnow() + timedelta(minutes=eta_minutes),
                            'intensity': latest_intensity,
                            'confidence': 'high' if eta_minutes <= 10 else 'medium',
                            'current_distance_km': latest_distance,
                            'velocity_kmh': velocity_km_per_hour,
                            'approaching': True
                        }
                    else:
                        print(f"[GlobalRadar] Rain is approaching but ETA ({eta_minutes:.0f} min) > threshold ({GlobalRadarService.PREDICTION_THRESHOLD_MINUTES} min)")
                        return None
                else:
                    print(f"[GlobalRadar] Rain detected at {latest_distance}km but moving away (distance_change: {distance_change:.1f}km)")
                    return None
            else:
                # Only one frame with rain detected
                print(f"[GlobalRadar] Rain detected at {latest_distance}km but not enough history to determine movement")

                # If rain is close, alert anyway
                if latest_distance <= 10:
                    return {
                        'minutes_until_rain': 5,
                        'expected_at': datetime.utcnow() + timedelta(minutes=5),
                        'intensity': latest_intensity,
                        'confidence': 'medium',
                        'current_distance_km': latest_distance,
                        'approaching': False
                    }

                return None

        except Exception as e:
            print(f"[GlobalRadar] Error checking location: {e}")
            import traceback
            traceback.print_exc()
            return None
