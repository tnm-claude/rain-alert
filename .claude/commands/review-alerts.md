# Rain Alert Review

You are reviewing the rain alert system's recent behavior to diagnose false positives, missed alerts, or notification issues.

## Architecture snapshot

**Detection pipeline:**
1. `app/scheduler.py` — APScheduler runs `check_all_locations()` every 5 minutes
2. `app/radar_global.py` — `GlobalRadarService.check_rain_at_location()` queries RainViewer tile API, analyzes last 4 radar frames across concentric radius zones (5/10/15/20 km), and returns rain info if rain is approaching
3. `app/weather.py` — `WeatherService` (Open-Meteo hourly forecast) — used only for forecasts, not for alert triggering
4. `app/notifications.py` — `NotificationService.send_alert()` dispatches to Slack (Block Kit with header + image), Telegram (plain text with location), and Email
5. `app/models.py` — `Alert` and `NotificationSettings` SQLite models via Flask-SQLAlchemy

**Key thresholds (radar_global.py):**
- `MIN_INTENSITY_THRESHOLD = 120` — alpha channel cutoff on RainViewer RGBA tiles
- `CHECK_RADIUS_KM = [5, 10, 15, 20]` — checked in order; first hit wins
- `PREDICTION_THRESHOLD_MINUTES = 30` — only alert if rain ETA ≤ 30 min
- `FRAMES_TO_ANALYZE = 4` — last ~40 minutes of radar frames
- Alert fires only if rain is *approaching* (distance decreasing over frames), or already at location (≤ 5 km)

**Cooldown (scheduler.py):**
- 30-minute cooldown per location, regardless of dismissed status
- Query: `Alert.location_id == location.id, Alert.created_at >= recent_cutoff` (no `dismissed` filter — dismissing an alert does NOT reset the cooldown)

**Notification settings** are stored in the `notification_settings` DB table. Telegram requires `telegram_enabled=True` plus `telegram_bot_token` and `telegram_chat_id`.

## Known past issues (fixed)

- **2026-04-08:** System sent alerts from 5am through ~11am; all but one were false positives. Root causes:
  - `MIN_INTENSITY_THRESHOLD` was 50 (too low — picked up faint radar artifacts)
  - `dismissed == False` was included in the cooldown query — dismissing an alert immediately re-enabled alerting
  - Rain within 10km always triggered regardless of movement direction
  - Slack messages lacked a distinct title (now uses `header` block)

- **Winter 2025-26 (#2):** no pre-alert radar was ever saved (55 alerts, 0 images): buffer files were
  named in local time while `created_at` is UTC. Fixed in #10: all capture files use UTC names and
  frames are fetched at alert time. Alerts before that have no `data/alerts/<id>/`.

## Diagnostic steps

When the user reports unexpected alerts or silence, do the following in order:

### 1. Read current config
```
app/radar_global.py        — thresholds, radius, frame count
app/scheduler.py           — cooldown logic, job interval
app/notifications.py       — Slack block structure, Telegram message format
```

### 2. Query the alerts DB
```bash
sqlite3 data/rain_alert.db "
SELECT id, alert_time, rain_expected_at, minutes_ahead, message, dismissed, user_feedback, created_at
FROM alerts
ORDER BY created_at DESC
LIMIT 20;"
```

Look for:
- Bursts of alerts within the same 30-min window (cooldown bypass)
- `user_feedback = 0` (false alarms) correlating with specific times/distances
- Alerts with no follow-up feedback (could be true or false — ask user)

### 2b. Look at what the detector saw (saved per alert)
Every alert since #10 has `data/alerts/<alert_id>/` (`alerts.radar_images_saved` = `alerts/<id>`).
All file names and JSON times are **UTC**:
- `ims_<UTC>.png` / `rv_<UTC>.png`: IMS (5-min) and RainViewer (10-min) frames from the 60 min before
  the alert, cropped to 120 km around the location, red marker = location, rings at 10/25/50 km
- `ims_raw_*.png` (940px IMS overlay), `rv_raw_*.png` (512px z7 tile, colour scheme 0, ~265 km across,
  centred on the location): unmodified source images for re-running detection offline
- `w2d_<UTC>.png`: weather2day radar at alert time (not georeferenced)
- `preview.png`: latest frame (sent with notifications); `detection.json`: diagnostics, thresholds,
  location, alert row, file list and capture errors

Open the frames with the Read tool (start with `preview.png`, then step through `ims_*.png`), or use
the `/review` page, which animates them and shows the detection.json summary. Check: was there rain
inside the 10/25 km rings, was it moving toward the marker, and do `detection.json` distance/ETA match
the frames?

### 2c. Per-check detection log (alerts and non-alerts)
```bash
sqlite3 data/rain_alert.db "
SELECT checked_at, location_id, should_alert, reason, round(distance_km,1), max_dbz, intensity,
       round(velocity_kmh), eta_minutes
FROM detection_checks
WHERE checked_at >= datetime('now', '-1 day')
ORDER BY checked_at DESC LIMIT 50;"
```
One row per location per 5-min check (UTC, kept 180 days); `details` holds compact JSON of the scalar
diagnostics. Join to alerts by `location_id` and time (`alerts.created_at` is within seconds of the
check that triggered it). Use it to find near-misses (rain close but `should_alert=0`) and to test new
thresholds against past checks.

Storage: `data/alerts` is capped at 500 MB (oldest unlabelled alerts are pruned first, then the
oldest labelled), so label alerts you want kept with ✓/✗.

### 3. Check notification settings
```bash
sqlite3 data/rain_alert.db "SELECT slack_enabled, telegram_enabled, telegram_bot_token, telegram_chat_id FROM notification_settings;"
```

### 4. Check logs
The scheduler prints to stdout. If the app runs as a service:
```bash
# Check system logs or wherever the process stdout is captured
journalctl -u rain-alert --since "yesterday" | grep -E "\[Scheduler\]|\[GlobalRadar\]|\[Notifications\]"
```

Look for `[GlobalRadar]` lines showing:
- Which frames had rain and at what distance
- Whether `distance_change` was positive (approaching) or negative (moving away)
- Whether the intensity threshold was crossed

### 5. Evaluate threshold changes

If too many false positives → raise `MIN_INTENSITY_THRESHOLD` (currently 120, range 50–200)
If missing real rain → lower threshold or expand `CHECK_RADIUS_KM`
If re-alerting too fast → extend the cooldown in `scheduler.py` (`timedelta(minutes=30)`)

## Files to read for any alert investigation
- `app/radar_global.py` — detection logic
- `app/scheduler.py` — cooldown, alert creation, notification dispatch
- `app/notifications.py` — message format for each channel
- `app/models.py` — Alert schema (especially `dismissed`, `user_feedback`, `radar_images_saved`) and `DetectionCheck`
- `app/capture.py` — what is saved per alert, pruning, the per-check log
