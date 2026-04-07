# Rain Alert 🌧️

Self-hosted rain monitoring and alert system for Israel with predictive nowcasting.

## Quick Start

```bash
# Start the application
./start.sh

# Access the UI
open http://127.0.0.1:5000

# Stop the application
./stop.sh

# Restart after changes
./restart.sh

# View logs
tail -f rain-alert.log
```

## What It Does

- 🔍 **Monitors locations** for approaching rain (10km radius)
- ⚡ **Predictive alerts** with ETA and velocity tracking
- 📱 **Multi-channel notifications** (Slack, Telegram, Email)
- 🎯 **Feedback system** to mark alerts as accurate/false (ML training data)
- 📊 **Radar visualization** with 3 different data sources
- 🕹️ **Windows 98 retro UI** because why not

## Current Status

✅ **Fully Functional**
- Server running on `127.0.0.1:5000`
- Scheduler checking every 5 minutes
- Alert radius: 10km
- No auto-dismiss (alerts persist until manually handled)
- 30-minute duplicate prevention per location

## Important Files

### Critical Data (DO NOT DELETE)
- `data/rain_alert.db` - All your data (locations, alerts, feedback)
- `data/radar/alerts/` - Saved radar images for ML training
- `.env` - Configuration and secrets

### Documentation
- `PROJECT_SUMMARY.md` - Complete documentation (READ THIS FIRST)
- `CRITICAL_FILES.txt` - Backup information
- `README.md` - This file

### Scripts
- `start.sh` - Start server
- `stop.sh` - Stop server
- `restart.sh` - Restart server
- `create_backup.sh` - Backup all data

## Setup

### First Time Setup

1. **Add Locations**
   - Open http://127.0.0.1:5000
   - Click "File" → "Add New Location"
   - Enter any address in Israel
   - Set one as "main" location (🏠 button)

2. **Configure Notifications (Optional)**
   - Click "File" → "Settings"
   - Enable Slack/Telegram/Email
   - Enter credentials
   - Test with "Test Notifications" button

3. **Start Monitoring**
   - Alerts will automatically trigger when rain detected
   - Mark alerts as accurate (✓) or false alarm (✗)
   - View history in "View" → "Alert Review"

### Telegram Setup

1. Create bot: Message **@BotFather** → `/newbot`
2. Get Chat ID: Message **@userinfobot**
3. **Important**: Start conversation with your bot
4. Configure in Settings with bot token + chat ID

### Slack Setup

1. Create incoming webhook in Slack workspace
2. Copy webhook URL
3. Configure in Settings
4. Notifications include radar image (no buttons)

## Daily Use

### Monitoring
- Open http://127.0.0.1:5000
- View active alerts
- Check radar (3 different views available via tabs)
- Mark alerts to improve accuracy

### Radar Views
- **RainViewer**: Local implementation with playback controls
- **Weather2day**: Embedded animated radar
- **Weather2day (Google Maps)**: Interactive map
- Set default tab with "Set Default" button

### Review Alerts
- Click "View" → "Alert Review"
- See all alerts with saved radar images
- Mark as accurate/false for ML training
- Builds dataset for future improvements

## Maintenance

### Backup Your Data

```bash
# Easy backup (recommended weekly)
./create_backup.sh

# Manual backup
cp data/rain_alert.db data/rain_alert_backup_$(date +%Y%m%d).db

# Full backup
tar -czf rain-alert-backup-$(date +%Y%m%d).tar.gz data/ .env
```

### Check Status

```bash
# Is server running?
ps aux | grep python | grep run.py

# Recent alerts
sqlite3 data/rain_alert.db "
  SELECT id, datetime(created_at, 'localtime'), message
  FROM alerts
  ORDER BY created_at DESC
  LIMIT 10;
"

# Active alerts
sqlite3 data/rain_alert.db "
  SELECT COUNT(*) FROM alerts WHERE dismissed=0;
"

# View logs
tail -100 rain-alert.log
```

### Troubleshooting

**Alerts not triggering?**
- Check logs: `tail -f rain-alert.log | grep Scheduler`
- Verify locations active: Visit main page
- Check 30-min cooldown: Recent alerts prevent duplicates

**Notifications not working?**
- Test via UI: Settings → Test Notifications
- For Telegram: Make sure you started bot conversation
- Check logs: `grep Notification rain-alert.log`

**UI not loading?**
- Hard refresh: Cmd+Shift+R (Mac) or Ctrl+Shift+R (Windows)
- Restart server: `./restart.sh`
- Check server status: `ps aux | grep python`

## How Alerts Work

1. **Every 5 minutes**: Scheduler checks all active locations
2. **Rain detected within 10km**: Alert created immediately
3. **Rain 10-30km away**: Alert if approaching with velocity
4. **30-minute cooldown**: No duplicate alerts for same location
5. **Notifications sent**: To all enabled channels
6. **Radar images saved**: Last 30 minutes for ML training
7. **Manual feedback**: Mark as accurate/false to improve algorithm

## Configuration

### Alert Settings
- **Radius**: 10km (configurable in `app/radar_global.py`)
- **Check interval**: 5 minutes (hardcoded in scheduler)
- **Duplicate prevention**: 30 minutes per location
- **Auto-dismiss**: Disabled (alerts persist)

### Notification Channels
- **Slack**: Webhook with radar image
- **Telegram**: Text notifications via bot
- **Email**: SMTP with configurable server

### Radar Sources
- **RainViewer API**: Free, global data, 10-min updates
- **Weather2day**: Israel-specific radar
- All sources used for visualization only
- Alerts use RainViewer data

## Database Queries

```bash
# Active locations
sqlite3 data/rain_alert.db "SELECT * FROM locations WHERE active=1;"

# Recent alerts
sqlite3 data/rain_alert.db "
  SELECT id, created_at, message, dismissed, user_feedback
  FROM alerts
  ORDER BY created_at DESC
  LIMIT 20;
"

# Feedback statistics
sqlite3 data/rain_alert.db "
  SELECT
    CASE
      WHEN user_feedback IS NULL THEN 'No Feedback'
      WHEN user_feedback = 1 THEN 'Accurate'
      ELSE 'False Alarm'
    END as type,
    COUNT(*) as count
  FROM alerts
  GROUP BY type;
"

# Accuracy per location
sqlite3 data/rain_alert.db "
  SELECT
    l.address,
    COUNT(*) as total_alerts,
    SUM(CASE WHEN a.user_feedback=1 THEN 1 ELSE 0 END) as accurate,
    SUM(CASE WHEN a.user_feedback=0 THEN 1 ELSE 0 END) as false_alarms
  FROM alerts a
  JOIN locations l ON a.location_id = l.id
  GROUP BY l.address;
"
```

## API Endpoints

Useful for automation or integration:

```bash
# Get active alerts
curl http://127.0.0.1:5000/api/alerts

# Add location
curl -X POST http://127.0.0.1:5000/api/locations \
  -H "Content-Type: application/json" \
  -d '{"address": "Tel Aviv, Israel"}'

# Mark alert as accurate
curl -X POST http://127.0.0.1:5000/api/alerts/123/feedback \
  -H "Content-Type: application/json" \
  -d '{"accurate": true}'

# Health check
curl http://127.0.0.1:5000/health
```

## Known Limitations

- 🏠 **Local only**: Runs on 127.0.0.1 (not publicly accessible)
- 👤 **Single user**: No authentication or multi-user support
- 🇮🇱 **Israel-focused**: Optimized for Israeli radar data
- 🆓 **Free tier**: RainViewer API limits (updates every ~10 min)
- 🤖 **No ML yet**: Collects training data but doesn't learn (yet)
- 🔔 **No mobile push**: Desktop notifications only

## Future Improvements

Ideas for enhancement (not implemented):
- Machine learning model for improved accuracy
- Multi-user support with authentication
- Mobile app with push notifications
- Configurable alert radius per location
- Storm tracking with trajectory visualization
- Weather forecast integration
- Export training dataset for external ML tools

## Project Structure

```
rain-alert/
├── app/                    # Application code
│   ├── models.py          # Database models
│   ├── routes.py          # Web routes & API
│   ├── scheduler.py       # Background jobs
│   ├── radar_global.py    # Rain detection algorithm
│   ├── notifications.py   # Slack/Telegram/Email
│   └── templates/         # HTML templates
├── data/                   # ⚠️ CRITICAL DATA
│   ├── rain_alert.db      # Database
│   └── radar/alerts/      # Training images
├── .env                    # Configuration
├── PROJECT_SUMMARY.md     # Full documentation
└── create_backup.sh       # Backup script
```

## Support & Documentation

- **Full Documentation**: See `PROJECT_SUMMARY.md`
- **Critical Files Info**: See `CRITICAL_FILES.txt`
- **Troubleshooting**: Check logs (`tail -f rain-alert.log`)
- **Database Issues**: Use SQL queries above
- **Backup Problems**: Run `./create_backup.sh`

## Version

**Current**: 2026-03-30
- Removed Slack buttons (no ngrok needed)
- Removed auto-dismiss (alerts persist)
- Added alert timestamps
- Tabbed radar interface
- Auto-play animation

## License

Personal project - Self-hosted use only

---

**Quick Reference:**
- Start: `./start.sh`
- Stop: `./stop.sh`
- Backup: `./create_backup.sh`
- Logs: `tail -f rain-alert.log`
- UI: http://127.0.0.1:5000

For complete documentation, see **PROJECT_SUMMARY.md**
