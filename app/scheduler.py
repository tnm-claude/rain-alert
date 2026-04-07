"""
Background scheduler for checking weather and creating alerts
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from app.models import db, Location, Alert, NotificationSettings
from app.weather import WeatherService
from app.notifications import NotificationService
from app.radar import RadarService
from app.radar_global import GlobalRadarService
from datetime import datetime, timedelta
import atexit
import logging

# Configure logging for scheduler
logging.basicConfig()
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

scheduler = BackgroundScheduler()
app_instance = None


def job_listener(event):
    """Listen to job execution events for debugging"""
    if event.exception:
        print(f'[Scheduler] Job {event.job_id} crashed: {event.exception}')
    else:
        print(f'[Scheduler] Job {event.job_id} completed successfully')


def check_all_locations():
    """Check weather for all active locations and create alerts if needed"""
    global app_instance

    with app_instance.app_context():
        locations = Location.query.filter_by(active=True).all()
        print(f"\n[Scheduler] ========== Checking {len(locations)} locations at {datetime.now()} ==========")

        for location in locations:
            try:
                print(f"[Scheduler] Checking: {location.address} ({location.latitude:.4f}, {location.longitude:.4f})")

                # Check if rain is present using global radar service
                rain_info = GlobalRadarService.check_rain_at_location(
                    location.latitude,
                    location.longitude
                )

                if rain_info:
                    minutes_until = rain_info['minutes_until_rain']
                    expected_at = rain_info['expected_at']
                    intensity = rain_info['intensity']
                    confidence = rain_info.get('confidence', 'medium')
                    current_distance = rain_info.get('current_distance_km', 0)
                    is_approaching = rain_info.get('approaching', False)
                    velocity = rain_info.get('velocity_kmh', 0)

                    print(f"[Scheduler] ✓ Rain detected: intensity={intensity}, confidence={confidence}, distance={current_distance}km")

                    # Check if we have a recent alert (within last 30 minutes) for this location
                    recent_cutoff = datetime.utcnow() - timedelta(minutes=30)
                    recent_alert = Alert.query.filter(
                        Alert.location_id == location.id,
                        Alert.dismissed == False,
                        Alert.created_at >= recent_cutoff
                    ).first()

                    if not recent_alert:
                        # Create new alert with predictive information
                        intensity_label = "Heavy" if intensity > 150 else "Moderate" if intensity > 80 else "Light"

                        if minutes_until == 0:
                            # Rain is currently at location
                            message = f"🌧️ {intensity_label} rain detected at {location.address} (NOW)"
                        elif is_approaching and velocity > 0:
                            # Rain is approaching with known velocity
                            message = f"⚠️ {intensity_label} rain approaching {location.address} - ETA: {minutes_until} minutes ({current_distance:.0f}km away, moving {velocity:.0f} km/h)"
                        else:
                            # Rain detected nearby but movement unclear
                            message = f"⚠️ {intensity_label} rain near {location.address} ({current_distance:.0f}km away)"

                        alert_threshold = minutes_until if minutes_until > 0 else 5

                        alert = Alert(
                            location_id=location.id,
                            alert_time=datetime.utcnow(),
                            rain_expected_at=expected_at,
                            minutes_ahead=alert_threshold,
                            message=message,
                            dismissed=False
                        )
                        db.session.add(alert)
                        db.session.commit()

                        print(f"[Scheduler] Created alert: {message}")

                        # Save last 30 minutes of radar images immediately when alert is created
                        saved_images = RadarService.save_alert_radar_images(alert)
                        if saved_images:
                            alert.radar_images_saved = ','.join(saved_images)
                            db.session.commit()
                            print(f"[Scheduler] Saved {len(saved_images)} radar images for alert {alert.id}")

                        # Send notifications
                        settings = NotificationSettings.query.first()
                        if settings:
                            NotificationService.send_alert(settings, message, alert)
                        else:
                            print("[Scheduler] No notification settings configured")
                    else:
                        print(f"[Scheduler] Recent alert exists for this location (created {recent_alert.created_at}, waiting 30 min minimum)")
                else:
                    print(f"[Scheduler] No rain detected for this location")

            except Exception as e:
                print(f"[Scheduler] Error checking location {location.id}: {e}")
                import traceback
                traceback.print_exc()

        print(f"[Scheduler] ========== Weather check completed ==========\n")


def fetch_radar_images():
    """Fetch latest radar image and cleanup old ones"""
    try:
        print(f"[Scheduler] Fetching radar images at {datetime.now()}")
        success, filename, timestamp = RadarService.fetch_latest_radar_image()

        if success:
            print(f"[Scheduler] Successfully fetched radar image: {filename}")
            # Cleanup old images
            RadarService.cleanup_old_images()
        else:
            print("[Scheduler] No new radar images available")

    except Exception as e:
        print(f"[Scheduler] Error fetching radar images: {e}")


def start_scheduler(app):
    """Start the background scheduler"""
    global app_instance
    app_instance = app

    if not scheduler.running:
        # Add event listener for debugging
        scheduler.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

        # Schedule weather check every 5 minutes
        scheduler.add_job(
            func=check_all_locations,
            trigger=IntervalTrigger(minutes=5),
            id='check_weather',
            name='Check weather for all locations',
            replace_existing=True
        )

        # Schedule radar image fetch every 5 minutes
        scheduler.add_job(
            func=fetch_radar_images,
            trigger=IntervalTrigger(minutes=5),
            id='fetch_radar',
            name='Fetch radar images',
            replace_existing=True
        )

        # Start the scheduler FIRST
        scheduler.start()
        print("[Scheduler] Background scheduler started (checks every 5 minutes)")

        # Print scheduled jobs
        jobs = scheduler.get_jobs()
        print(f"[Scheduler] Scheduled jobs: {[job.id for job in jobs]}")

        # Run initial checks after scheduler is started (in background to avoid blocking startup)
        # The scheduler will run them on schedule anyway
        # try:
        #     fetch_radar_images()
        #     check_all_locations()
        # except Exception as e:
        #     print(f"[Scheduler] Error in initial run: {e}")

        # Shut down scheduler on app exit
        atexit.register(lambda: scheduler.shutdown())
