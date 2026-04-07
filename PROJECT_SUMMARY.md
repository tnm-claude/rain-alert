# Rain Alert Project - Complete Documentation

## Project Overview
Rain Alert is a self-hosted web application that monitors locations in Israel for rain using real-time radar data. It provides predictive alerts before rain arrives, sends notifications via Slack/Telegram/Email, and includes a feedback system for training future ML models.

**Tech Stack:**
- Flask (Python web framework)
- SQLite database
- APScheduler (background jobs)
- OpenLayers (map display)
- RainViewer API (radar data source for alerts)
- Weather2day.co.il (radar visualization)
- Windows 98 retro UI theme

**Current Status:** Fully functional, running on localhost:5000

## Project Structure

```
/Users/yoav.magor/dev/rain-alert/
├── app/
│   ├── __init__.py           # Flask app initialization
│   ├── models.py             # Database models (Location, Alert, NotificationSettings)
│   ├── routes.py             # Web routes and API endpoints
│   ├── scheduler.py          # Background jobs (weather checks every 5 min)
│   ├── weather.py            # Geocoding service
│   ├── notifications.py      # Slack/Telegram/Email notifications
│   ├── radar.py              # Radar image management
│   ├── radar_global.py       # Rain detection algorithm
│   ├── templates/            # HTML templates
│   │   ├── base.html
│   │   ├── index.html        # Main page
│   │   └── review.html       # Alert review page
│   └── static/
│       └── css/
│           └── windows98.css # Retro styling
├── data/
│   ├── rain_alert.db         # SQLite database ⚠️ CRITICAL DATA
│   └── radar/                # Radar image storage
│       └── alerts/           # Alert feedback images (30 min before each alert)
├── .env                      # Configuration
├── run.py                    # Application entry point
├── start.sh                  # Start server script
├── stop.sh                   # Stop server script
├── restart.sh                # Restart server script
└── rain-alert.log            # Application logs
```

## Critical Files (DO NOT DELETE)

### Data Files - Contain All Your Work:
1. **`data/rain_alert.db`** - SQLite database with all locations, alerts, feedback
2. **`data/radar/alerts/`** - Saved radar images for ML training (30 min before each alert)
3. **`.env`** - Configuration settings

### If These Are Lost:
- All location data is gone
- All alert history is gone
- All feedback data for ML training is gone

### Backup Command:
```bash
# Create backup
cp -r data/ data_backup_$(date +%Y%m%d_%H%M%S)/

# Or backup database only
cp data/rain_alert.db data/rain_alert_backup_$(date +%Y%m%d_%H%M%S).db
```

## Key Features

### 1. **Location Monitoring**
- Add multiple locations by address (geocoded via OpenStreetMap)
- Mark one location as "main" (displayed prominently)
- Locations shown with coordinates on map
- Remove/activate locations (soft delete - data preserved)

### 2. **Rain Detection & Alerts**
- Checks every 5 minutes using RainViewer API radar data
- **Alert radius: 10km** from monitored location
- Predictive: Detects approaching rain and estimates ETA
- **30-minute duplicate prevention**: Only one alert per location per 30 minutes
- Alert intensities: Light, Moderate, Heavy (based on radar reflectivity)
- **Alerts do NOT auto-dismiss** - they stay active until manually dismissed or marked

### 3. **Multi-Channel Notifications**
- **Slack**: Webhook with radar image from weather2day.co.il
- **Telegram**: Bot notifications (requires bot token + chat ID)
- **Email**: SMTP notifications
- Configured via Settings dialog in UI

### 4. **Alert Feedback System**
- Mark alerts as True (accurate) or False (false alarm)
- Marking true/false auto-dismisses the alert
- **Saves last 30 minutes of radar images** when alert is created
- Alert Review page shows all alerts with saved radar images
- Color coding: Gray (no feedback), Blue (true), Red (false)
- Data stored for future ML training

### 5. **Radar Visualization (Tabbed Interface)**
- **RainViewer Tab**: OpenLayers map with animation controls
  - Fixed zoom level 7 (shows entire Israel)
  - Last 12 frames (~2 hours)
  - Play/Pause/Prev/Next controls
  - Speed selector (Slow/Normal/Fast)
  - Auto-plays on page load
- **Weather2day Tab**: Embedded animated radar from weather2day.co.il
- **Weather2day (Google Maps) Tab**: Interactive Leaflet-based radar
- **Set Default**: Choose which tab loads by default (saved in localStorage)

## Database Schema

### `locations` table
```sql
CREATE TABLE locations (
    id INTEGER PRIMARY KEY,
    address TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    active BOOLEAN DEFAULT 1,
    is_main BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### `alerts` table
```sql
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL,
    alert_time TIMESTAMP NOT NULL,
    rain_expected_at TIMESTAMP,
    minutes_ahead INTEGER,
    message TEXT NOT NULL,
    dismissed BOOLEAN DEFAULT 0,
    user_feedback BOOLEAN,  -- True=accurate, False=false alarm, None=no feedback
    feedback_timestamp TIMESTAMP,
    radar_images_saved TEXT,  -- Comma-separated filenames
    slack_dismissed_until TIMESTAMP,  -- Not currently used
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (location_id) REFERENCES locations(id)
);
```

### `notification_settings` table
```sql
CREATE TABLE notification_settings (
    id INTEGER PRIMARY KEY,
    -- Slack
    slack_enabled BOOLEAN DEFAULT 0,
    slack_webhook_url TEXT,
    -- Telegram
    telegram_enabled BOOLEAN DEFAULT 0,
    telegram_bot_token TEXT,
    telegram_chat_id TEXT,
    -- Email
    email_enabled BOOLEAN DEFAULT 0,
    email_address TEXT,
    email_smtp_server TEXT,
    email_smtp_port INTEGER,
    email_smtp_user TEXT,
    email_smtp_password TEXT
);
```

## How It Works

### Alert Detection Flow
1. **Scheduler runs every 5 minutes** (`check_all_locations()` in `app/scheduler.py`)
2. For each active location:
   - Fetches latest RainViewer radar data (last 3-4 frames)
   - Analyzes radar reflectivity within 30km search radius
   - Detects rain intensity, distance, velocity, direction
   - **If rain within 10km → ALWAYS alert**
   - If rain between 10-30km → Check if approaching + calculate ETA
   - Checks if alert already exists for this location in last 30 minutes
   - If no recent alert: Creates new alert
3. **When alert is created:**
   - Saves alert to database with prediction details
   - **Immediately saves last 30 minutes of radar images** to `data/radar/alerts/`
   - Sends notifications to all enabled channels (Slack/Telegram/Email)

### Radar Image Management
- RainViewer images fetched every 5 minutes by scheduler
- Stored in `data/radar/` as `radar_YYYYMMDDHHMMSS.png`
- Old images cleaned up automatically (keeps recent history)
- Alert feedback images stored permanently in `data/radar/alerts/`
- Named: `alert_{alert_id}_radar_{timestamp}.png`
- These images are used for ML training (review alerts with radar context)

### Navigation
- **Main Page (`/`)**: Location management, active alerts, radar tabs
- **Alert Review (`/review`)**: All triggered alerts with radar images and feedback buttons
- **Settings (File → Settings)**: Configure notification channels

### API Endpoints

#### Location Management
- `POST /api/locations` - Add new location (geocodes address)
- `DELETE /api/locations/<id>` - Remove location (soft delete)
- `POST /api/locations/<id>/set-main` - Set location as main

#### Alert Management
- `GET /api/alerts` - Get active alerts (dismissed=False)
- `POST /api/alerts/<id>/dismiss` - Dismiss alert manually
- `POST /api/alerts/<id>/feedback` - Mark alert as true/false (auto-dismisses)

#### Settings & Testing
- `GET /api/settings` - Get notification settings
- `POST /api/settings` - Update notification settings
- `POST /api/test-notifications` - Send test notification to all enabled channels
- `POST /api/test-alert` - Create a test alert (for testing notifications)

#### Radar Images
- `GET /radar/<filename>` - Serve radar image
- `GET /radar-feedback/<filename>` - Serve alert feedback image

#### Legacy Endpoints (No Longer Used)
- `/api/alerts/<id>/slack-dismiss` - Slack button callback (buttons removed)
- `/api/alerts/<id>/slack-feedback` - Slack button callback (buttons removed)

## Current Configuration (.env)

```bash
# Application Settings
SECRET_KEY=f9373a84bab6902900237cc6414f0ee16b28bf3db7b2df367cf1392defc64e59
DATABASE_URL=sqlite:///data/rain_alert.db

# Environment
FLASK_ENV=development
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
DEBUG=true

# Public URL (not used - Slack buttons removed)
APP_URL=http://127.0.0.1:5000

# Geocoding
GEOCODING_SERVICE=osm

# Radar Configuration
RADAR_CHECK_INTERVAL_MINUTES=10
RADAR_CACHE_HOURS=24
RADAR_CACHE_PATH=./cache/radar
ALERT_THRESHOLDS=30,20,10

# Advanced
LOG_LEVEL=INFO
MAX_LOCATIONS_PER_USER=10
RATE_LIMIT_PER_MINUTE=60
USE_PLAYWRIGHT=false

# Notification settings configured via UI, not .env
```

## Running the Application

### Start/Stop Commands
```bash
# Start server (background process)
./start.sh

# Stop server
./stop.sh

# Restart server (stop + start)
./restart.sh

# View logs in real-time
tail -f rain-alert.log

# Check if server is running
ps aux | grep python | grep run.py

# Access UI
open http://127.0.0.1:5000
```

### Manual Start (for debugging)
```bash
# Activate virtual environment
source .venv/bin/activate

# Run directly
python run.py
```

## Recent Changes & Current State

### Latest Session Changes (2026-03-30):
1. ✅ **Removed Slack interactive buttons** - Slack shows only message + radar image
2. ✅ **Removed auto-dismiss after 30 minutes** - Alerts persist until manually dismissed
3. ✅ **Added alert timestamps to UI** - Shows "Triggered: YYYY-MM-DD HH:MM:SS"
4. ✅ **Implemented tabbed radar interface** - 3 radar sources with default tab selection
5. ✅ **Marking alerts as true/false auto-dismisses** - Both in UI and via API
6. ✅ **Auto-play radar animation** - Animation starts automatically on page load

### Alert Behavior (IMPORTANT):
- **Creation**: Alert triggered when rain detected within 10km
- **Duplicate Prevention**: 30-minute cooldown per location (prevents spam alerts)
- **Persistence**: Alerts stay visible until manually dismissed or marked as true/false
- **No auto-expiration** - Alerts do NOT automatically dismiss after 30 minutes
- **30-minute rule applies to CREATION only**, not dismissal

### Scheduler Jobs (runs every 5 minutes):
1. `check_weather` - Check all active locations for rain, create alerts
2. `fetch_radar` - Download latest radar images from RainViewer

**Removed Jobs:**
- ~~`cleanup_alerts`~~ - Auto-dismiss job removed (no longer auto-dismissing)

## Important Configuration Details

### Alert Radius
- **10km** - Configured in `app/radar_global.py` around line 45
- If rain within 10km: Always create alert (high confidence)
- If rain between 10-30km: Only alert if approaching with velocity
- UI Help and About dialogs reflect this 10km radius

### Time Zones
- Database stores **UTC timestamps**
- Display converts to **local time (IDT/IST)**
- Scheduler runs on **system local time**
- `datetime.utcnow()` used throughout code

### Radar Data Sources
- **Alerts use RainViewer API** (global radar data, free tier)
- **UI displays 3 radar options** (user can switch via tabs):
  1. RainViewer (custom OpenLayers implementation)
  2. Weather2day static/animated (iframe embed)
  3. Weather2day Google Maps interactive (iframe embed)

### Slack Integration
- Webhook URL configured via UI Settings
- Message format: Alert text + location + radar image
- Image URL: `https://www.weather2day.co.il/radar.php` (live radar snapshot)
- **No interactive buttons** (removed - no external callback needed)
- Just informational notifications

### Telegram Setup Instructions
1. Open Telegram, search for **@BotFather**
2. Send `/newbot` and follow prompts
3. Copy the **Bot Token** (format: `123456789:ABCdef...`)
4. Search for **@userinfobot**, send any message
5. Copy your **Chat ID** (format: `123456789`)
6. **CRITICAL**: Search for your bot and click "Start" or send `/start`
   - Bot cannot message you until you start the conversation
7. Configure in UI: File → Settings → Telegram section
8. Test with "Test Notifications" button

## Alert Detection Algorithm

Located in `app/radar_global.py`

### Key Parameters
```python
ALERT_DISTANCE_KM = 10        # Alert if rain within this distance
SEARCH_RADIUS_KM = 30         # Search for rain within this radius
INTENSITY_THRESHOLD = 30       # Minimum radar reflectivity (dBZ)
MOVEMENT_THRESHOLD_KMH = 5    # Minimum velocity to be "approaching"
```

### Algorithm Logic
```python
def check_rain_at_location(lat, lon):
    # 1. Fetch last 3-4 radar frames from RainViewer
    frames = fetch_recent_frames()

    # 2. Search pixels within 30km radius
    for frame in frames:
        for pixel in search_area:
            if pixel.intensity > INTENSITY_THRESHOLD:
                distance = haversine_distance(lat, lon, pixel.lat, pixel.lon)

                # 3. Track rain positions across frames
                rain_positions.append((distance, intensity, timestamp))

    # 4. Find closest rain
    closest = min(rain_positions, key=lambda x: x[0])

    # 5. If within 10km → ALWAYS ALERT
    if closest.distance <= 10:
        return create_alert(distance, intensity, eta=estimate_eta())

    # 6. If 10-30km away → Check if approaching
    if closest.distance <= 30:
        velocity = calculate_velocity_from_frames(frames)
        if velocity > 5 and is_approaching(velocity_vector):
            eta = calculate_eta(distance, velocity)
            return create_alert(distance, intensity, eta, velocity)

    # 7. No alert if rain too far or not approaching
    return None
```

### Intensity Levels
```python
if intensity > 150:
    level = "Heavy"    # Dark red on radar
elif intensity > 80:
    level = "Moderate" # Orange/yellow on radar
else:
    level = "Light"    # Light yellow/green on radar
```

## Known Issues / Limitations

### Current Limitations:
1. **Local development only** - Runs on 127.0.0.1:5000 (not publicly accessible)
2. **Single user** - No authentication or multi-user support
3. **Israel-focused** - Radar data and detection optimized for Israel
4. **RainViewer API limitations** - Free tier, updates every ~10 minutes
5. **No ML model yet** - Feedback system collects data but doesn't train/improve algorithm
6. **Slack buttons removed** - No interactive buttons (would require ngrok/public URL)

### False Positives/Negatives:
- **Too late alerts**: Happens when rain moves faster than expected or appears suddenly
- **False positives**: Can occur due to radar artifacts or non-rain precipitation
- **Use feedback system**: Mark alerts to build training data for future ML improvements

## Future Improvements (Not Implemented)

Potential enhancements:
- [ ] ML model to improve alert accuracy using feedback data
- [ ] Multi-user support with authentication (OAuth)
- [ ] Mobile app / push notifications
- [ ] Configurable alert radius per location
- [ ] Historical alert performance analytics
- [ ] Integration with more radar sources (IMS, European data)
- [ ] Export alert data / training dataset to CSV
- [ ] Weather forecast integration (not just radar)
- [ ] Storm tracking with trajectory visualization
- [ ] Custom alert sounds/tones
- [ ] Webhook support for external integrations

## Troubleshooting

### Alerts Not Triggering

**Symptoms:** No alerts created even when it's raining

**Debugging Steps:**
```bash
# 1. Check logs for scheduler activity
tail -f rain-alert.log | grep Scheduler

# 2. Verify scheduler is running (should see every 5 minutes)
# Look for: "[Scheduler] ========== Checking X locations at ..."

# 3. Check active locations
sqlite3 data/rain_alert.db "SELECT id, address, active FROM locations;"

# 4. Verify radar images are downloading
ls -lth data/radar/ | head -10

# 5. Check if recent alerts exist (30-min cooldown)
sqlite3 data/rain_alert.db "
  SELECT id, datetime(created_at, 'localtime') as created, dismissed, message
  FROM alerts
  WHERE created_at > datetime('now', '-1 hour')
  ORDER BY created_at DESC;
"

# 6. Manually test radar detection
python -c "
from app.radar_global import GlobalRadarService
result = GlobalRadarService.check_rain_at_location(32.0853, 34.7818)  # Tel Aviv
print(result)
"
```

**Common Causes:**
- No active locations configured
- Recent alert exists (30-minute cooldown active)
- RainViewer API temporarily down
- Rain is outside 10km radius
- Scheduler not running (server crashed)

### Notifications Not Sending

**Symptoms:** Alerts created but no Slack/Telegram/Email received

**Debugging Steps:**
```bash
# 1. Test via UI
# Go to Settings → Test Notifications

# 2. Check notification settings
sqlite3 data/rain_alert.db "SELECT * FROM notification_settings;"

# 3. Check logs for notification errors
tail -f rain-alert.log | grep Notification

# 4. For Telegram: Verify bot conversation started
# Open Telegram, search for your bot, click "Start"

# 5. For Slack: Verify webhook URL
# Test webhook manually:
curl -X POST -H 'Content-Type: application/json' \
  -d '{"text": "Test from command line"}' \
  YOUR_SLACK_WEBHOOK_URL
```

**Common Causes:**
- Notification channel not enabled in settings
- Invalid credentials (bot token, webhook URL, SMTP password)
- Telegram: Didn't start conversation with bot
- Email: Firewall blocking SMTP port 587
- Network connectivity issues

### UI Issues

**Symptoms:** Page not loading, old data showing, JavaScript errors

**Solutions:**
```bash
# 1. Hard refresh browser
# Mac: Cmd + Shift + R
# Windows: Ctrl + Shift + R

# 2. Check server is running
ps aux | grep python | grep run.py

# 3. Check logs for errors
tail -50 rain-alert.log

# 4. Restart server
./restart.sh

# 5. Check browser console
# Open Developer Tools (F12) → Console tab
# Look for JavaScript errors

# 6. Clear localStorage (if tabs not working)
# Browser Console:
localStorage.clear()
location.reload()
```

### Database Issues

**Symptoms:** Errors about missing tables, data corruption

**Check Database Health:**
```bash
# View schema
sqlite3 data/rain_alert.db ".schema"

# Check integrity
sqlite3 data/rain_alert.db "PRAGMA integrity_check;"

# Count records
sqlite3 data/rain_alert.db "
  SELECT 'Locations' as table_name, COUNT(*) as count FROM locations
  UNION ALL
  SELECT 'Alerts', COUNT(*) FROM alerts
  UNION ALL
  SELECT 'Settings', COUNT(*) FROM notification_settings;
"

# View recent alerts
sqlite3 data/rain_alert.db "
  SELECT
    id,
    datetime(created_at, 'localtime') as created,
    dismissed,
    user_feedback,
    substr(message, 1, 60) as message
  FROM alerts
  ORDER BY created_at DESC
  LIMIT 10;
"

# Check for duplicate locations
sqlite3 data/rain_alert.db "
  SELECT address, COUNT(*) as count
  FROM locations
  WHERE active=1
  GROUP BY address
  HAVING count > 1;
"
```

**Backup Before Fixing:**
```bash
cp data/rain_alert.db data/rain_alert_backup_$(date +%Y%m%d_%H%M%S).db
```

### Radar Images Not Saving

**Symptoms:** Alerts created but `radar_images_saved` is NULL

**Check:**
```bash
# 1. Verify radar directory exists
ls -la data/radar/alerts/

# 2. Check permissions
ls -ld data/radar/
# Should be: drwxr-xr-x

# 3. Check disk space
df -h

# 4. Look for errors in logs
grep -i "radar" rain-alert.log | grep -i "error"

# 5. Check if source images exist
ls -lth data/radar/ | head -10
```

## Quick Reference Commands

### Server Management
```bash
./start.sh               # Start server in background
./stop.sh                # Stop server
./restart.sh             # Restart server
tail -f rain-alert.log   # View logs in real-time
ps aux | grep python     # Check if running
```

### Database Queries
```bash
# Active locations
sqlite3 data/rain_alert.db "
  SELECT id, address, latitude, longitude, is_main
  FROM locations
  WHERE active=1;
"

# Recent alerts
sqlite3 data/rain_alert.db "
  SELECT
    id,
    datetime(created_at, 'localtime') as created,
    dismissed,
    user_feedback,
    message
  FROM alerts
  ORDER BY created_at DESC
  LIMIT 10;
"

# Active alerts
sqlite3 data/rain_alert.db "
  SELECT COUNT(*) as active_alerts
  FROM alerts
  WHERE dismissed=0;
"

# Feedback statistics
sqlite3 data/rain_alert.db "
  SELECT
    CASE
      WHEN user_feedback IS NULL THEN 'No Feedback'
      WHEN user_feedback = 1 THEN 'Accurate'
      ELSE 'False Alarm'
    END as feedback_type,
    COUNT(*) as count
  FROM alerts
  GROUP BY feedback_type;
"

# Alerts per location
sqlite3 data/rain_alert.db "
  SELECT
    l.address,
    COUNT(*) as alert_count,
    SUM(CASE WHEN a.user_feedback=1 THEN 1 ELSE 0 END) as accurate,
    SUM(CASE WHEN a.user_feedback=0 THEN 1 ELSE 0 END) as false_alarms
  FROM alerts a
  JOIN locations l ON a.location_id = l.id
  GROUP BY l.address;
"
```

### Backup Commands
```bash
# Full backup
tar -czf rain-alert-backup-$(date +%Y%m%d_%H%M%S).tar.gz \
  data/ .env

# Database only
cp data/rain_alert.db \
  data/rain_alert_backup_$(date +%Y%m%d_%H%M%S).db

# Restore from backup
cp data/rain_alert_backup_YYYYMMDD_HHMMSS.db data/rain_alert.db
./restart.sh
```

### Development
```bash
# Activate virtual environment
source .venv/bin/activate

# Install new package
pip install package-name
pip freeze > requirements.txt

# Update dependencies
pip install -r requirements.txt --upgrade

# Run Flask shell (for debugging)
flask shell

# Database migrations (if needed)
flask db migrate -m "description"
flask db upgrade
```

## Monitoring & Maintenance

### Daily Checks
```bash
# 1. Verify server is running
ps aux | grep python | grep run.py

# 2. Check recent alerts
sqlite3 data/rain_alert.db "
  SELECT COUNT(*) FROM alerts
  WHERE created_at > datetime('now', '-1 day');
"

# 3. Check disk space (radar images can accumulate)
du -sh data/radar/
df -h

# 4. Review logs for errors
tail -100 rain-alert.log | grep -i error
```

### Weekly Maintenance
```bash
# 1. Create backup
./create_backup.sh

# 2. Clean old radar images (automatic but verify)
ls -lth data/radar/ | wc -l

# 3. Review alert accuracy
sqlite3 data/rain_alert.db "
  SELECT
    COUNT(*) as total,
    SUM(CASE WHEN user_feedback=1 THEN 1 ELSE 0 END) as accurate,
    SUM(CASE WHEN user_feedback=0 THEN 1 ELSE 0 END) as false_alarms,
    SUM(CASE WHEN user_feedback IS NULL THEN 1 ELSE 0 END) as no_feedback
  FROM alerts
  WHERE created_at > datetime('now', '-7 days');
"
```

### Monthly Tasks
```bash
# 1. Export training data
sqlite3 data/rain_alert.db -header -csv "
  SELECT
    a.*,
    l.latitude,
    l.longitude,
    l.address
  FROM alerts a
  JOIN locations l ON a.location_id = l.id
  WHERE a.user_feedback IS NOT NULL
" > alert_training_data_$(date +%Y%m).csv

# 2. Archive old logs
mv rain-alert.log rain-alert-$(date +%Y%m).log
touch rain-alert.log

# 3. Check database size
ls -lh data/rain_alert.db
```

## URLs & Resources

### Application URLs
- **Main UI**: http://127.0.0.1:5000
- **Alert Review**: http://127.0.0.1:5000/review
- **Health Check**: http://127.0.0.1:5000/health

### External APIs Used
- **RainViewer API**: https://api.rainviewer.com/public/weather-maps.json
  - Free tier, no API key needed
  - Updates every ~10 minutes
  - Global radar data
- **OpenStreetMap Nominatim**: https://nominatim.openstreetmap.org/
  - Free geocoding service
  - Rate limited: 1 request/second
- **Weather2day Radar**: https://www.weather2day.co.il/radar.php
  - Used for Slack image
  - Israel-specific radar

### Documentation
- **Flask**: https://flask.palletsprojects.com/
- **APScheduler**: https://apscheduler.readthedocs.io/
- **OpenLayers**: https://openlayers.org/
- **Slack Block Kit**: https://api.slack.com/block-kit
- **Telegram Bot API**: https://core.telegram.org/bots/api

## Environment Variables Reference

```bash
# Application
SECRET_KEY                    # Flask secret key for sessions
DATABASE_URL                  # SQLite database path
FLASK_ENV                     # development/production
FLASK_HOST                    # Default: 127.0.0.1
FLASK_PORT                    # Default: 5000
DEBUG                         # true/false
APP_URL                       # Public URL (not used currently)

# Geocoding
GEOCODING_SERVICE            # osm (OpenStreetMap Nominatim)

# Radar
RADAR_CHECK_INTERVAL_MINUTES # Not used (hardcoded to 5)
RADAR_CACHE_HOURS            # 24
RADAR_CACHE_PATH             # ./cache/radar
ALERT_THRESHOLDS             # 30,20,10 (not fully used)

# Advanced
LOG_LEVEL                    # INFO
MAX_LOCATIONS_PER_USER       # 10
RATE_LIMIT_PER_MINUTE        # 60
USE_PLAYWRIGHT               # false
```

## Contact & Support

Since this is a self-hosted personal project, support is self-service:

1. **Check logs**: `tail -f rain-alert.log`
2. **Review this documentation**: Most issues are covered above
3. **Database queries**: Use the SQL examples provided
4. **Restart server**: `./restart.sh` fixes many issues
5. **Backup first**: Before making any changes

## Version History

- **2026-03-30**: Current version
  - Removed Slack buttons
  - Removed auto-dismiss (alerts persist until manually dismissed)
  - Added alert timestamps to UI
  - Added tabbed radar interface
  - Auto-play radar animation
  - Feedback auto-dismisses alerts

- **2026-03-29**: Previous session
  - Fixed radar zoom issues
  - Changed alert radius to 10km
  - Added alert feedback system
  - Implemented radar image saving (30 min before alerts)
  - Created Alert Review page

- **2026-03-27**: Earlier development
  - Initial implementation
  - Basic alert system
  - Slack/Telegram/Email notifications
  - Windows 98 UI theme

## Final Notes

### Data Preservation
Your most important data is in:
1. `data/rain_alert.db` - All locations, alerts, feedback
2. `data/radar/alerts/` - Training data (radar images)
3. `.env` - Configuration

**Backup these regularly!**

### Performance
- Server uses minimal resources (~50-100 MB RAM)
- Scheduler runs every 5 minutes (low CPU usage)
- Disk usage grows with radar images (auto-cleaned)
- SQLite handles thousands of alerts without issues

### Security Notes
- No authentication (local use only)
- Sensitive data in `.env` (SMTP passwords, API tokens)
- Database not encrypted
- **Do not expose to public internet without authentication**

### Next Steps
When continuing work:
1. Check server status: `ps aux | grep python`
2. Review recent alerts: Check database
3. Read logs: `tail -100 rain-alert.log`
4. Test notifications: Use Settings → Test Notifications
5. Review feedback: http://127.0.0.1:5000/review

---

**Last Updated**: 2026-03-30
**Status**: Production-ready for local use
**Maintainer**: Self-hosted personal project
