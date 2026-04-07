"""
Radar image service for fetching and managing radar images
"""
import requests
import os
from datetime import datetime, timedelta
from pathlib import Path


class RadarService:
    """Service for fetching Israeli radar images from RainViewer API"""

    # RainViewer API
    API_URL = "https://api.rainviewer.com/public/weather-maps.json"
    MAX_IMAGES = 12  # Keep last 2 hours (12 images at 10 min intervals)

    # Israel center coordinates for radar tiles
    ISRAEL_LAT = 31.5  # Center of Israel
    ISRAEL_LON = 34.8
    ZOOM_LEVEL = 7  # Maximum zoom for detailed view

    @staticmethod
    def get_radar_directory():
        """Get the radar images storage directory"""
        basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        radar_dir = os.path.join(basedir, 'data', 'radar')
        Path(radar_dir).mkdir(parents=True, exist_ok=True)
        return radar_dir

    @staticmethod
    def fetch_all_radar_images():
        """
        Fetch last 12 radar images (2 hours) from RainViewer API
        Returns: number of images fetched
        """
        try:
            # Get available radar frames from RainViewer API
            response = requests.get(RadarService.API_URL, timeout=10)
            if response.status_code != 200:
                print(f"[Radar] API returned status {response.status_code}")
                return 0

            data = response.json()
            radar_frames = data.get('radar', {}).get('past', [])

            if not radar_frames:
                print("[Radar] No radar frames available from API")
                return 0

            # Get the host URL
            host = data.get('host', 'https://tilecache.rainviewer.com')

            # Fetch last 12 frames (or all available if less than 12)
            frames_to_fetch = radar_frames[-12:] if len(radar_frames) >= 12 else radar_frames
            fetched_count = 0

            for frame in frames_to_fetch:
                timestamp = frame.get('time')
                path = frame.get('path')

                if not timestamp or not path:
                    continue

                # Convert Unix timestamp to datetime
                frame_time = datetime.fromtimestamp(timestamp)
                timestamp_str = frame_time.strftime("%Y%m%d%H%M")
                filename = f"radar_{timestamp_str}.png"
                filepath = os.path.join(RadarService.get_radar_directory(), filename)

                # Skip if already exists
                if os.path.exists(filepath):
                    fetched_count += 1
                    continue

                # Build tile URL using coordinate-based format for Israel
                # Format: {host}{path}/{size}/{z}/{lat}/{lon}/{color}/{options}.png
                # size=512, z=7 (max zoom), color=2 (default), smooth=1, snow=0
                tile_url = f"{host}{path}/512/{RadarService.ZOOM_LEVEL}/{RadarService.ISRAEL_LAT}/{RadarService.ISRAEL_LON}/2/1_0.png"

                try:
                    tile_response = requests.get(tile_url, timeout=10)

                    # Verify it's a valid PNG
                    if (tile_response.status_code == 200 and
                        len(tile_response.content) > 1000 and
                        tile_response.content[:4] == b'\x89PNG'):

                        with open(filepath, 'wb') as f:
                            f.write(tile_response.content)

                        print(f"[Radar] Fetched: {filename} ({len(tile_response.content)} bytes)")
                        fetched_count += 1
                    else:
                        print(f"[Radar] Invalid response for {filename}")

                except Exception as e:
                    print(f"[Radar] Error fetching {filename}: {e}")

            print(f"[Radar] Successfully fetched {fetched_count}/{len(frames_to_fetch)} images")
            return fetched_count

        except Exception as e:
            print(f"[Radar] Error in fetch_all_radar_images: {e}")
            return 0

    @staticmethod
    def fetch_latest_radar_image():
        """
        Fetch the latest radar images (backwards compatible)
        Returns: tuple of (success, filename, timestamp) or (False, None, None)
        """
        count = RadarService.fetch_all_radar_images()
        if count > 0:
            # Get the most recent image
            images = RadarService.get_available_images()
            if images:
                latest = images[-1]
                timestamp = datetime.fromisoformat(latest['timestamp'])
                return True, latest['filename'], timestamp

        return False, None, None

    @staticmethod
    def cleanup_old_images():
        """Remove old radar images, keeping only the last MAX_IMAGES"""
        try:
            radar_dir = RadarService.get_radar_directory()
            images = []

            # Get all radar images with their timestamps
            for filename in os.listdir(radar_dir):
                if filename.startswith('radar_') and filename.endswith('.png'):
                    filepath = os.path.join(radar_dir, filename)
                    mtime = os.path.getmtime(filepath)
                    images.append((mtime, filepath))

            # Sort by modification time (newest first)
            images.sort(reverse=True)

            # Remove old images beyond MAX_IMAGES
            if len(images) > RadarService.MAX_IMAGES:
                for _, filepath in images[RadarService.MAX_IMAGES:]:
                    try:
                        os.remove(filepath)
                        print(f"[Radar] Removed old image: {os.path.basename(filepath)}")
                    except Exception as e:
                        print(f"[Radar] Error removing {filepath}: {e}")

        except Exception as e:
            print(f"[Radar] Error during cleanup: {e}")

    @staticmethod
    def get_available_images():
        """
        Get list of available radar images sorted by timestamp
        Returns: list of dicts with filename and timestamp
        """
        try:
            radar_dir = RadarService.get_radar_directory()
            images = []

            for filename in os.listdir(radar_dir):
                if filename.startswith('radar_') and filename.endswith('.png'):
                    # Extract timestamp from filename: radar_YYYYMMDDHHMM.png
                    try:
                        timestamp_str = filename.replace('radar_', '').replace('.png', '')
                        timestamp = datetime.strptime(timestamp_str, '%Y%m%d%H%M')
                        images.append({
                            'filename': filename,
                            'timestamp': timestamp.isoformat(),
                            'display_time': timestamp.strftime('%H:%M')
                        })
                    except:
                        continue

            # Sort by timestamp (oldest first for animation)
            images.sort(key=lambda x: x['timestamp'])

            return images

        except Exception as e:
            print(f"[Radar] Error getting available images: {e}")
            return []

    @staticmethod
    def save_alert_radar_images(alert):
        """
        Save radar images from last 30 minutes before alert time
        Returns: list of saved image filenames
        """
        try:
            from shutil import copy2

            radar_dir = RadarService.get_radar_directory()
            feedback_dir = os.path.join(radar_dir, 'alerts')
            Path(feedback_dir).mkdir(parents=True, exist_ok=True)

            # Get images from last 30 minutes before alert
            cutoff_time = alert.created_at - timedelta(minutes=30)
            images = []

            for filename in os.listdir(radar_dir):
                if filename.startswith('radar_') and filename.endswith('.png'):
                    try:
                        timestamp_str = filename.replace('radar_', '').replace('.png', '')
                        timestamp = datetime.strptime(timestamp_str, '%Y%m%d%H%M')

                        # Only include images from last 30 minutes before alert
                        if cutoff_time <= timestamp <= alert.created_at:
                            images.append((timestamp, filename))
                    except:
                        continue

            if not images:
                print(f"[Radar] No images found in last 30 min before alert time {alert.created_at}")
                return []

            # Sort by timestamp (oldest first)
            images.sort()
            saved_filenames = []

            for timestamp, filename in images:
                # Create new filename: alert_alertID_originalname
                new_filename = f"alert_{alert.id}_{filename}"
                src_path = os.path.join(radar_dir, filename)
                dst_path = os.path.join(feedback_dir, new_filename)

                try:
                    copy2(src_path, dst_path)
                    saved_filenames.append(new_filename)
                except Exception as e:
                    print(f"[Radar] Error saving {filename}: {e}")

            print(f"[Radar] Saved {len(saved_filenames)} images (last 30 min) for alert {alert.id}")
            return saved_filenames

        except Exception as e:
            print(f"[Radar] Error in save_alert_radar_images: {e}")
            return []
