"""
Background scheduler for checking weather and creating alerts
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from app.models import db, Location, Alert, NotificationSettings
from app.notifications import NotificationService
from app.radar import RadarService
from app import capture
from app.radar_global import GlobalRadarService
from app import detection, health
from datetime import datetime, timedelta
import atexit
import logging

# Configure logging for scheduler
logging.basicConfig()
logging.getLogger('apscheduler').setLevel(logging.WARNING)

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

                # Radar analysis: always returns diagnostics (should_alert, reason, per-frame stats)
                diagnostics = GlobalRadarService.analyze_location(
                    location.latitude,
                    location.longitude
                )
                capture.log_check(location, diagnostics)  # one detection_checks row per location per check
                rain_info = diagnostics if diagnostics.get('should_alert') else None

                if rain_info:
                    minutes_until = rain_info['minutes_until_rain']
                    expected_at = rain_info['expected_at']

                    print(f"[Scheduler] ✓ Rain alert condition: {rain_info['reason']}")

                    # Event-based suppression: one alert per rain event. A new alert fires only after
                    # the area was clear of rain for a while (see detection.EVENT_CLEAR_*). Dismissed
                    # alerts count too, so dismissing never causes an immediate re-alert.
                    last_alert = Alert.query.filter(
                        Alert.location_id == location.id
                    ).order_by(Alert.created_at.desc()).first()
                    new_event, event_reason = detection.is_new_event(
                        diagnostics['frames'], last_alert.created_at if last_alert else None
                    )
                    diagnostics['event'] = event_reason
                    recent_alert = None if new_event else last_alert

                    if not recent_alert:
                        message = detection.build_message(location.address, rain_info)
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

                        # Save the 60 min of radar before the alert to data/alerts/<id>/ (bounded, never raises;
                        # sets alert.radar_images_saved). preview.png in that dir is for notifications.
                        alert_dir = capture.snapshot_alert(alert, rain_info)

                        # Send notifications
                        # settings may be None: channels configured only via .env still send
                        settings = NotificationSettings.query.first()
                        NotificationService.send_alert(
                            settings, message, alert,
                            image_path=capture.preview_path(alert_dir), diagnostics=rain_info)
                    else:
                        print(f"[Scheduler] Suppressed: {event_reason}")
                else:
                    print(f"[Scheduler] No alert: {diagnostics['reason']}")

            except Exception as e:
                print(f"[Scheduler] Error checking location {location.id}: {e}")
                import traceback
                traceback.print_exc()

        print("[Scheduler] ========== Weather check completed ==========\n")
        health.mark('location_check')


def fetch_radar_images():
    """Refresh the rolling RainViewer buffer (<= 2 h, UTC names; fallback for alert capture)
    and run the daily data/alerts + detection_checks prune"""
    try:
        if RadarService.fetch_all_radar_images():
            health.mark('radar_fetch')
    except Exception as e:
        print(f"[Scheduler] Error fetching radar images: {e}")
    finally:
        # Always cleanup old images, regardless of fetch outcome
        RadarService.cleanup_old_images()
        with app_instance.app_context():
            capture.maintenance_if_due()


def poll_telegram():
    """Record Telegram feedback-button taps (✅/❌) into Alert.user_feedback"""
    try:
        with app_instance.app_context():
            NotificationService.poll_telegram_updates()
    except Exception as e:
        print(f"[Scheduler] Telegram poll error: {e}")


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
            next_run_time=datetime.now() + timedelta(seconds=30),
            name='Check weather for all locations',
            replace_existing=True
        )

        # Schedule radar image fetch every 5 minutes
        scheduler.add_job(
            func=fetch_radar_images,
            trigger=IntervalTrigger(minutes=5),
            id='fetch_radar',
            next_run_time=datetime.now() + timedelta(seconds=15),
            name='Fetch radar images',
            replace_existing=True
        )

        # Poll Telegram for feedback button taps (long-poll 10s, so ~continuous)
        scheduler.add_job(
            func=poll_telegram,
            trigger=IntervalTrigger(seconds=15),
            id='poll_telegram',
            name='Poll Telegram feedback',
            replace_existing=True,
            max_instances=1,
            coalesce=True
        )

        # Start the scheduler FIRST
        scheduler.start()
        print("[Scheduler] Background scheduler started (checks every 5 minutes)")

        # Print scheduled jobs
        jobs = scheduler.get_jobs()
        print(f"[Scheduler] Scheduled jobs: {[job.id for job in jobs]}")

        # Shut down scheduler on app exit
        atexit.register(lambda: scheduler.shutdown())
