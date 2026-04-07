# 🌧️ Continue Working on Rain Alert

**Quick guide for returning to this project in a new session**

---

## 🚀 Getting Started (3 Steps)

### 1. Check Server Status
```bash
cd /Users/yoav.magor/dev/rain-alert
ps aux | grep python | grep run.py
```

**If server is running:** You'll see output like:
```
yoav.magor  61779  ... python run.py
```
✅ **Server is running** → Skip to step 3

**If no output:** Server is stopped
```bash
./start.sh
```

### 2. Verify Everything Works
```bash
# Check logs for errors
tail -50 rain-alert.log

# Open the UI
open http://127.0.0.1:5000

# Check scheduler is running
tail -f rain-alert.log | grep Scheduler
# Press Ctrl+C to stop watching
```

### 3. Review Current State
```bash
# How many locations are monitored?
sqlite3 data/rain_alert.db "SELECT COUNT(*) FROM locations WHERE active=1;"

# Recent alerts (last 24 hours)
sqlite3 data/rain_alert.db "
  SELECT
    id,
    datetime(created_at, 'localtime') as time,
    CASE
      WHEN dismissed=0 THEN 'ACTIVE'
      ELSE 'dismissed'
    END as status,
    substr(message, 1, 60) as message
  FROM alerts
  WHERE created_at > datetime('now', '-1 day')
  ORDER BY created_at DESC;
"

# Alerts waiting for feedback
sqlite3 data/rain_alert.db "
  SELECT COUNT(*) FROM alerts WHERE user_feedback IS NULL;
"
```

---

## 📖 Essential Documentation

### Read These First
1. **PROJECT_SUMMARY.md** - Complete technical documentation (COMPREHENSIVE)
2. **README.md** - Quick start guide and daily use
3. **CRITICAL_FILES.txt** - What files to never delete

### Quick References
- **Server commands**: `./start.sh`, `./stop.sh`, `./restart.sh`
- **Backup**: `./create_backup.sh`
- **Logs**: `tail -f rain-alert.log`
- **UI**: http://127.0.0.1:5000

---

## 🎯 Current Project Status

### ✅ Working Features
- Rain detection (10km radius)
- Predictive alerts with ETA
- Slack notifications (with radar image)
- Telegram notifications
- Email notifications
- Feedback system (mark as true/false)
- Alert Review page with radar images
- 3 radar views (tabbed interface)
- Auto-play radar animation
- Windows 98 UI theme

### ⚙️ Current Configuration
- **Alert radius**: 10km
- **Check interval**: Every 5 minutes
- **Duplicate prevention**: 30 minutes per location
- **Auto-dismiss**: DISABLED (alerts persist)
- **Slack buttons**: REMOVED (no external URL needed)

### 🗂️ Your Data
```bash
# Database location
data/rain_alert.db

# Training data (radar images)
data/radar/alerts/

# Backups
backups/
```

---

## 🔧 Common Tasks

### Add a New Location
1. Open http://127.0.0.1:5000
2. File → Add New Location
3. Enter address (e.g., "Herzliya, Israel")
4. Optionally set as main (🏠 button)

### Review & Mark Alerts
1. View → Alert Review
2. See all alerts with saved radar images
3. Mark each as ✓ True or ✗ False
4. Builds training data for ML

### Configure Notifications
1. File → Settings
2. Enable Slack/Telegram/Email
3. Enter credentials
4. Click "Test Notifications"
5. Save Settings

### Check Alert Accuracy
```bash
sqlite3 data/rain_alert.db "
  SELECT
    CASE
      WHEN user_feedback IS NULL THEN 'No Feedback'
      WHEN user_feedback = 1 THEN 'Accurate'
      ELSE 'False Alarm'
    END as feedback,
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM alerts), 1) as percent
  FROM alerts
  GROUP BY feedback;
"
```

### Create Backup
```bash
./create_backup.sh
```
This creates:
- `backups/rain-alert-backup-YYYYMMDD_HHMMSS.tar.gz`
- `backups/alert_training_data_YYYYMMDD.csv` (if you have feedback)

---

## 🐛 Troubleshooting

### "Server won't start"
```bash
# Check what's using port 5000
lsof -i :5000

# Kill old process
ps aux | grep python | grep run.py | awk '{print $2}' | xargs kill

# Try again
./start.sh
```

### "No alerts appearing"
```bash
# Check scheduler
tail -100 rain-alert.log | grep Scheduler

# Check locations
sqlite3 data/rain_alert.db "SELECT * FROM locations WHERE active=1;"

# Check if rain detected recently
tail -50 rain-alert.log | grep "Rain detected"

# Check 30-min cooldown
sqlite3 data/rain_alert.db "
  SELECT
    l.address,
    datetime(a.created_at, 'localtime') as last_alert,
    ROUND((julianday('now') - julianday(a.created_at)) * 24 * 60, 1) as minutes_ago
  FROM alerts a
  JOIN locations l ON a.location_id = l.id
  WHERE a.dismissed=0
  ORDER BY a.created_at DESC;
"
```

### "Notifications not sending"
```bash
# Check settings
sqlite3 data/rain_alert.db "SELECT * FROM notification_settings;"

# Test from UI
# Go to Settings → Test Notifications

# Check logs
tail -100 rain-alert.log | grep Notification
```

### "UI showing old data"
```bash
# Hard refresh browser
# Mac: Cmd + Shift + R
# Windows: Ctrl + Shift + R

# Or clear cache
# Browser → Settings → Clear browsing data
```

---

## 📊 Useful Database Queries

```bash
# Active locations with coordinates
sqlite3 data/rain_alert.db "
  SELECT id, address, latitude, longitude, is_main
  FROM locations
  WHERE active=1;
"

# Alerts from today
sqlite3 data/rain_alert.db "
  SELECT
    id,
    datetime(created_at, 'localtime') as time,
    CASE WHEN dismissed=0 THEN '🔴 ACTIVE' ELSE '⚫ dismissed' END as status,
    message
  FROM alerts
  WHERE date(created_at) = date('now')
  ORDER BY created_at DESC;
"

# Accuracy by location
sqlite3 data/rain_alert.db "
  SELECT
    l.address,
    COUNT(*) as total,
    SUM(CASE WHEN a.user_feedback=1 THEN 1 ELSE 0 END) as accurate,
    SUM(CASE WHEN a.user_feedback=0 THEN 1 ELSE 0 END) as false_alarms,
    ROUND(
      SUM(CASE WHEN a.user_feedback=1 THEN 1 ELSE 0 END) * 100.0 /
      NULLIF(SUM(CASE WHEN a.user_feedback IS NOT NULL THEN 1 ELSE 0 END), 0),
      1
    ) as accuracy_percent
  FROM alerts a
  JOIN locations l ON a.location_id = l.id
  WHERE a.user_feedback IS NOT NULL
  GROUP BY l.address;
"

# Recent radar image count
find data/radar/alerts -name "*.png" | wc -l
```

---

## 🔄 After Making Code Changes

```bash
# 1. Stop server
./stop.sh

# 2. Make your changes
# Edit files in app/ directory

# 3. Restart server
./start.sh

# 4. Check logs for errors
tail -50 rain-alert.log

# 5. Test in browser
open http://127.0.0.1:5000
```

---

## 💾 Before Major Changes

**ALWAYS backup first!**

```bash
./create_backup.sh
```

Then proceed with changes. If something breaks:
```bash
# List backups
ls -lh backups/

# Restore from backup
tar -xzf backups/rain-alert-backup-YYYYMMDD_HHMMSS.tar.gz
cp rain-alert-backup-YYYYMMDD_HHMMSS/rain_alert.db data/rain_alert.db
./restart.sh
```

---

## 🎓 Learning the Codebase

### Key Files to Understand

1. **app/scheduler.py** - Background jobs (check every 5 min)
2. **app/radar_global.py** - Rain detection algorithm
3. **app/routes.py** - Web routes and API endpoints
4. **app/models.py** - Database schema
5. **app/notifications.py** - Slack/Telegram/Email
6. **app/templates/index.html** - Main UI

### Alert Flow
```
1. Scheduler runs (every 5 min)
   ↓
2. Fetch RainViewer radar data
   ↓
3. Check each active location (10km radius)
   ↓
4. Rain detected? → Create alert
   ↓
5. Save 30 min of radar images
   ↓
6. Send notifications (Slack/Telegram/Email)
   ↓
7. Show in UI (mark as true/false)
```

### Making Changes

**Change alert radius:**
- Edit `app/radar_global.py` line ~45
- Change `if latest_distance <= 10:` to desired km
- Update UI text in templates

**Change check interval:**
- Edit `app/scheduler.py` line ~174
- Change `IntervalTrigger(minutes=5)` to desired interval

**Add new notification channel:**
- Add to `app/notifications.py`
- Add to `app/models.py` (NotificationSettings)
- Add to UI in `app/templates/settings.html`

---

## 📱 Quick Contact Info

Since this is self-hosted, if you have issues:
1. Check logs: `tail -f rain-alert.log`
2. Read docs: `PROJECT_SUMMARY.md`
3. Query database: See SQL examples above
4. Backup and restore: `./create_backup.sh`

---

## ✅ Session Checklist

When starting a new session:
- [ ] Check server is running
- [ ] Review recent alerts
- [ ] Check for errors in logs
- [ ] Create backup before changes
- [ ] Read relevant docs
- [ ] Test changes before committing

When ending a session:
- [ ] Create backup
- [ ] Document any changes made
- [ ] Server can stay running (it's stable)
- [ ] Note any issues to fix next time

---

## 🎯 Next Steps (Ideas)

If you want to improve the project:

1. **Short term:**
   - Add more locations
   - Review and mark more alerts (build training data)
   - Fine-tune alert radius for your needs
   - Export training data: `backups/alert_training_data_*.csv`

2. **Medium term:**
   - Implement ML model using collected feedback
   - Add weather forecast integration
   - Create mobile-friendly view
   - Add storm tracking/trajectory

3. **Long term:**
   - Multi-user support
   - Public deployment
   - Mobile app
   - API for external integrations

---

**Remember:** Your data is in `data/rain_alert.db` and `data/radar/alerts/`
**Always backup before major changes:** `./create_backup.sh`

For complete documentation, see **PROJECT_SUMMARY.md** (comprehensive guide)
