"""
Weather API integration using Open-Meteo (free, no API key)
Geocoding using Nominatim/OSM (free, no API key)
"""
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple


class WeatherService:
    """Handle weather forecast and geocoding"""

    OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

    @staticmethod
    def geocode_address(address: str) -> Optional[Tuple[float, float, str]]:
        """
        Convert address to coordinates using Nominatim.

        Args:
            address: Address string

        Returns:
            Tuple of (latitude, longitude, display_name) or None if not found
        """
        try:
            response = requests.get(
                WeatherService.NOMINATIM_URL,
                params={
                    'q': address,
                    'format': 'json',
                    'limit': 1
                },
                headers={'User-Agent': 'RainAlert/1.0'},
                timeout=10
            )
            response.raise_for_status()
            results = response.json()

            if results:
                result = results[0]
                lat = float(result['lat'])
                lon = float(result['lon'])
                display_name = result.get('display_name', address)
                return (lat, lon, display_name)

            return None

        except Exception as e:
            print(f"Geocoding error for '{address}': {e}")
            return None

    @staticmethod
    def get_rain_forecast(latitude: float, longitude: float) -> List[Dict]:
        """
        Get hourly rain forecast for the next 12 hours.

        Args:
            latitude: Location latitude
            longitude: Location longitude

        Returns:
            List of forecast data points with time and precipitation
        """
        try:
            response = requests.get(
                WeatherService.OPEN_METEO_URL,
                params={
                    'latitude': latitude,
                    'longitude': longitude,
                    'hourly': 'precipitation',
                    'forecast_days': 1,
                    'timezone': 'auto'
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            # Parse hourly data
            forecast = []
            if 'hourly' in data:
                times = data['hourly'].get('time', [])
                precip = data['hourly'].get('precipitation', [])

                for time_str, precip_val in zip(times, precip):
                    # Parse ISO timestamp
                    time_obj = datetime.fromisoformat(time_str.replace('Z', '+00:00'))

                    forecast.append({
                        'time': time_obj,
                        'precipitation': precip_val if precip_val is not None else 0.0
                    })

            return forecast

        except Exception as e:
            print(f"Weather forecast error for ({latitude}, {longitude}): {e}")
            return []

    @staticmethod
    def check_incoming_rain(latitude: float, longitude: float) -> Optional[Dict]:
        """
        Check if rain is expected within the next 40 minutes.

        Args:
            latitude: Location latitude
            longitude: Location longitude

        Returns:
            Dict with rain info if rain expected, None otherwise:
            {
                'minutes_until_rain': 25,
                'expected_at': datetime_object,
                'intensity': 1.5
            }
        """
        forecast = WeatherService.get_rain_forecast(latitude, longitude)
        if not forecast:
            return None

        now = datetime.now(forecast[0]['time'].tzinfo) if forecast else datetime.now()

        # Check next 40 minutes for rain
        for entry in forecast:
            time_diff = (entry['time'] - now).total_seconds() / 60  # minutes
            precipitation = entry['precipitation']

            # Rain is expected if precipitation > 0.1mm
            if 0 < time_diff <= 40 and precipitation > 0.1:
                return {
                    'minutes_until_rain': int(time_diff),
                    'expected_at': entry['time'],
                    'intensity': precipitation
                }

        return None
