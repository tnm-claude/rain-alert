# Context for New AI Sessions

**READ THIS FIRST before making any changes to the rain-alert system.**

---

## Critical Information

### Current Working State ✅

The system is **WORKING CORRECTLY** as of 2026-03-23. It successfully:
- Detects rain globally (tested with Turkey locations)
- Sends Slack notifications
- Displays interactive radar map without errors
- Runs scheduled checks every 10 minutes

### Data Source - READ CAREFULLY

**ONLY use RainViewer API for rain detection.**

```python
# ✅ CORRECT - This is what works:
from app.radar_global import GlobalRadarService
rain_info = GlobalRadarService.check_rain_at_location(lat, lon)
```

```python
# ❌ WRONG - Never use these:
from app.weather import WeatherService  # Only for geocoding
from app.radar_nowcast import RadarNowcastService  # Legacy, Israel-only
# Do NOT use Open-Meteo or any weather forecast APIs
```

---

## Common Mistakes That Were Made (Don't Repeat)

### Mistake #1: Using Weather Forecast APIs ❌

**What happened:** Initial implementation used Open-Meteo forecast API
**Why it's wrong:** Forecasts are not accurate for nowcasting
**User feedback:** "i explicitly told you that open meteo is not good for this task"

**Solution:** Use GlobalRadarService which reads actual radar pixels

### Mistake #2: Assuming Israel-Only Coverage ❌

**What happened:** Claimed system only works in Israel
**Why it's wrong:** RainViewer provides global radar tiles
**User feedback:** "i see the percipitation layer in locations not over israel, so why are you lying"

**Solution:** GlobalRadarService works globally - fetches tiles on-demand for any location

### Mistake #3: Limiting Map Zoom ❌

**What happened:** Set maxZoom: 9 to "prevent issues"
**Why it's wrong:** Disabled zoom functionality instead of fixing the real issue
**User feedback:** "you just disabled the option to zoom in more, instead of fixing the actual issue"

**Solution:** Set maxZoom: 15, maxNativeZoom: 8, errorTileUrl: '' - allows full zoom with scaled tiles

### Mistake #4: Creating Separate Pages ❌

**What happened:** Created `/add-location` route with separate page
**Why it's wrong:** User wants everything on main page
**User feedback:** "create a pop-up with the same windows 98 design - no need for a new screen"

**Solution:** Use modal dialogs for all actions

---

## Architecture Quick Reference

```
User Locations (lat, lon)
    ↓
Scheduler (every 10 minutes)
    ↓
GlobalRadarService.check_rain_at_location()
    ↓
Fetches RainViewer tile: {host}{path}/256/{zoom}/{x}/{y}/2/1_0.png
    ↓
Reads pixel RGBA at exact coordinates
    ↓
If alpha > 0: Rain detected
    ↓
Create Alert + Send Notifications (Slack/Email/Telegram)
```

**Key Point:** Each location check = 1 HTTP request to fetch 1 tile, read 1 pixel.

---

## Files You Might Need to Modify

### For Alert Detection Logic:
- `app/radar_global.py` - GlobalRadarService (rain detection)
- `app/scheduler.py` - Background jobs

### For API Endpoints:
- `app/routes.py` - Web routes and API endpoints

### For UI Changes:
- `app/templates/index.html` - Main page
- `app/static/css/windows98.css` - UI theme

### For Notifications:
- `app/notifications.py` - NotificationService
- `app/models.py` - NotificationSettings model

---

## Testing Checklist

Before claiming "it works":

1. ✅ Add a location in Turkey or anywhere with current rain
2. ✅ Wait 10 minutes for scheduler to run
3. ✅ Verify alert appears in UI
4. ✅ Verify notification sent to Slack/Email/Telegram
5. ✅ Zoom in on map - verify radar overlay stays visible
6. ✅ No "zoom level not supported" messages

---

## User Preferences

1. **Windows 98 UI:** Keep the retro theme, no modern Material/Bootstrap
2. **Modal Dialogs:** Use popups instead of separate pages
3. **Minimal Design:** User explicitly requested minimal approach
4. **No Over-Engineering:** Don't add features not requested
5. **Real Radar Data:** Never use forecast APIs for rain detection

---

## Quick Commands

```bash
# Start server
source .venv/bin/activate && python run.py

# Check scheduler logs
grep "Checking:" /tmp/claude/-Users-yoav-magor-dev-rain-alert/tasks/*.output

# Database queries
sqlite3 data/rain_alert.db "SELECT * FROM locations WHERE active=1;"
sqlite3 data/rain_alert.db "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 5;"

# Test alert (via browser)
# Click "🧪 Create Test Alert" button on main page
```

---

## What NOT to Change

1. **GlobalRadarService detection algorithm** - It works globally
2. **Scheduler interval (10 minutes)** - Matches radar update frequency
3. **RainViewer as data source** - User explicitly chose this
4. **Windows 98 UI theme** - User preference
5. **Modal dialog pattern** - User requested this

---

## Current Known Issues

**NONE - System is working as expected.**

If you encounter issues:
1. Check RainViewer API is accessible
2. Verify database exists at `data/rain_alert.db`
3. Check scheduler logs for errors
4. Test with location known to have current rain

---

## For Detailed Information

See `TECHNICAL_OVERVIEW.md` for:
- Complete architecture details
- API specifications
- Database schema
- Code examples
- Extension guidelines

---

**Last Working State Verified:** 2026-03-23 20:16 IST
**Alerts Sent Successfully:** 2 (Mersin Turkey, Konya Turkey)
**System Status:** Production Ready ✅
