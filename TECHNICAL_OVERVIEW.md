# Rain Alert System - Technical Overview

**Last Updated:** 2026-03-23
**Status:** Working - Ready for Feature Extensions
**Version:** 1.0

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [Data Sources & APIs](#data-sources--apis)
4. [Core Components](#core-components)
5. [Alert Detection System](#alert-detection-system)
6. [Frontend Implementation](#frontend-implementation)
7. [Database Schema](#database-schema)
8. [Configuration](#configuration)
9. [Known Limitations](#known-limitations)
10. [Critical Design Decisions](#critical-design-decisions)
11. [File Structure](#file-structure)

---

## System Overview

**Purpose:** Real-time rain detection and alerting system that monitors specific geographic locations worldwide and sends notifications when precipitation is detected.

**Key Features:**
- Global rain detection using radar imagery
- Interactive radar map viewer with 2-hour playback
- Multi-location monitoring (unlimited locations worldwide)
- Multi-channel notifications (Slack, Telegram, Email)
- Windows 98 retro UI theme
- 10-minute check intervals
- Test alert functionality

**Tech Stack:**
- Backend: Flask 3.0, SQLAlchemy, APScheduler
- Frontend: Vanilla JavaScript, Leaflet.js, Windows 98 CSS
- Data: RainViewer API (global radar tiles)
- Database: SQLite
- Image Processing: Pillow, NumPy

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Flask Web Server                       │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐  │
│  │   Web Routes   │  │  API Endpoints │  │  Static CSS  │  │
│  └────────────────┘  └────────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │   Scheduler  │  │   Database   │  │ Notification │
    │ (APScheduler)│  │   (SQLite)   │  │   Service    │
    └──────────────┘  └──────────────┘  └──────────────┘
            │                                    │
            ▼                                    ▼
    ┌──────────────┐                    ┌──────────────┐
    │ GlobalRadar  │                    │ Slack/Email/ │
    │   Service    │                    │   Telegram   │
    └──────────────┘                    └──────────────┘
            │
            ▼
    ┌──────────────┐
    │  RainViewer  │
    │   API (CDN)  │
    └──────────────┘
```

### Component Flow

1. **Scheduler** runs every 10 minutes
2. Queries active locations from **Database**
3. For each location, calls **GlobalRadarService**
4. GlobalRadarService fetches real-time radar tiles from **RainViewer API**
5. Analyzes pixel data at location coordinates
6. If rain detected, creates **Alert** in database
7. **NotificationService** sends alerts via configured channels
8. **Frontend** displays alerts and interactive radar map

---

## Data Sources & APIs

### RainViewer API (Primary Data Source)

**CRITICAL:** This is the ONLY data source for rain detection. Do NOT use weather forecast APIs.

**Base URL:** `https://api.rainviewer.com/public/weather-maps.json`

**Returns:**
```json
{
  "host": "https://tilecache.rainviewer.com",
  "radar": {
    "past": [
      {"time": 1711209600, "path": "/v2/radar/1711209600/256"}
    ]
  }
}
```

**Tile Access Patterns:**

1. **Frontend Map Display** (Global Tiles):
   ```
   {host}{path}/256/{z}/{x}/{y}/2/1_0.png
   ```
   - `{z}` = zoom level (1-8 supported)
   - `{x}/{y}` = tile coordinates
   - Works globally

2. **Backend Detection** (Coordinate-Based):
   ```python
   # Calculate tile coordinates from lat/lon
   x_tile = int((lon + 180) / 360 * 2**zoom)
   y_tile = int((1 - asinh(tan(lat_rad)) / pi) / 2 * 2**zoom)

   # Fetch tile
   tile_url = f"{host}{path}/256/{zoom}/{x_tile}/{y_tile}/2/1_0.png"

   # Calculate pixel within tile
   pixel_x = int((x_tile_float - x_tile) * 256)
   pixel_y = int((y_tile_float - y_tile) * 256)

   # Read RGBA value
   r, g, b, a = image.getpixel((pixel_x, pixel_y))

   # alpha > 0 = rain present
   ```

**Tile Properties:**
- Format: PNG with RGBA channels
- Size: 256x256 pixels
- Update frequency: ~10 minutes
- Coverage: Global
- Max zoom: 8 (higher zoom scales existing tiles)
- Alpha channel: 0 = no rain, 1-255 = increasing intensity

**Important:** Do NOT attempt to download/store all tiles. Fetch on-demand per location.

---

## Core Components

### 1. GlobalRadarService (`app/radar_global.py`)

**Purpose:** Detect rain at ANY global location using RainViewer's tile server.

**Key Methods:**

```python
class GlobalRadarService:
    @staticmethod
    def check_rain_at_location(lat: float, lon: float) -> Optional[Dict]:
        """
        Returns:
        {
            'minutes_until_rain': 5,  # Always 5 for current rain
            'expected_at': datetime,
            'intensity': 0-255,  # Alpha channel value
            'confidence': 'high' | 'medium' | 'low'
        }
        OR None if no rain detected
        """
```

**Algorithm:**
1. Fetch latest radar frame metadata from API
2. Calculate tile coordinates for location
3. Fetch tile image: `{host}{path}/256/{zoom}/{x}/{y}/2/1_0.png`
4. Calculate pixel coordinates within tile
5. Read RGBA value at exact location
6. If alpha > 0: rain detected
7. Return intensity and confidence based on alpha value

**Coverage:** Truly global - works anywhere RainViewer has radar data.

### 2. Scheduler (`app/scheduler.py`)

**Purpose:** Background job scheduling for periodic checks.

**Jobs:**
- `check_all_locations`: Runs every 10 minutes, checks all active locations for rain
- `fetch_radar_images`: Runs every 10 minutes, pre-fetches Israel radar tiles for frontend display

**Alert Creation Logic:**
```python
if rain_detected:
    # Check for existing recent alert (within 15 min)
    if not recent_alert_exists:
        create_alert()
        send_notifications()
```

**Deduplication:** Prevents duplicate alerts within 15-minute window per location.

### 3. NotificationService (`app/notifications.py`)

**Supported Channels:**

1. **Slack:**
   ```python
   webhook_url = settings.slack_webhook_url
   # POST JSON with 'text' field
   ```

2. **Telegram:**
   ```python
   bot_token = settings.telegram_bot_token
   chat_id = settings.telegram_chat_id
   # POST to api.telegram.org/bot{token}/sendMessage
   ```

3. **Email:**
   ```python
   smtp_server = settings.email_smtp_server
   smtp_port = settings.email_smtp_port
   # SMTP with STARTTLS
   ```

**Success Criteria:** At least one channel succeeds = notification sent.

### 4. RadarService (`app/radar.py`)

**Purpose:** Pre-fetch Israel-centered radar tiles for frontend display only.

**IMPORTANT:** This service is ONLY for UI display. It is NOT used for alert detection.

**What it does:**
- Downloads 12 radar tiles (2 hours) centered on Israel (31.5°N, 34.8°E)
- Stores locally in `data/radar/` directory
- Frontend `/api/radar/images` endpoint serves these files
- Cleanup: Removes tiles older than 12 frames

**What it does NOT do:**
- Alert detection (use GlobalRadarService instead)
- Global coverage (Israel only)

---

## Alert Detection System

### Detection Pipeline

```
Location (lat, lon)
    ↓
GlobalRadarService.check_rain_at_location()
    ↓
Fetch RainViewer tile for location
    ↓
Read pixel RGBA at exact coordinates
    ↓
If alpha > 0:
    ↓
Create Alert record
    ↓
Send Notifications
```

### Rain Detection Criteria

```python
# Pixel RGBA values
r, g, b, a = pixel_value

if a > 0:  # Any alpha = rain present
    intensity = a  # 0-255 scale

    if intensity > 150:
        level = "Heavy"
        confidence = "high"
    elif intensity > 80:
        level = "Moderate"
        confidence = "high" if intensity > 100 else "medium"
    else:
        level = "Light"
        confidence = "medium"
```

### Alert Thresholds

- **10-minute threshold:** Used for all detected rain (immediate)
- **Deduplication:** 15-minute window prevents duplicate alerts
- **Auto-dismiss:** User can manually dismiss alerts via UI

### Why This Approach Works

1. **Real-time:** Uses actual radar data, not forecasts
2. **Accurate:** Pixel-level precision at location coordinates
3. **Global:** Works anywhere RainViewer has coverage
4. **Simple:** Single API call per location per check
5. **Reliable:** No complex nowcasting algorithms required

---

## Frontend Implementation

### Technology Stack

- **Leaflet.js 1.9.4:** Interactive mapping
- **CartoDB Voyager:** Base map tiles
- **RainViewer Tiles:** Radar overlay
- **Vanilla JavaScript:** No frameworks
- **Windows 98 CSS:** Retro UI theme

### Map Configuration

```javascript
// Map initialization
map = L.map('radar-map', {
    center: [31.5, 34.8],  // Israel center
    zoom: 7,
    minZoom: 5,
    maxZoom: 15  // Full zoom range
});

// Base layer
L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    maxZoom: 19,
    subdomains: 'abcd'
});

// Radar overlay
radarLayer = L.tileLayer(tileUrl, {
    tileSize: 256,
    opacity: 0.6,
    zIndex: 10,
    maxZoom: 15,
    maxNativeZoom: 8,  // RainViewer max native zoom
    errorTileUrl: ''  // Hide error tiles
});
```

### Radar Viewer Features

1. **Animation Controls:**
   - Play/Pause toggle
   - Previous/Next frame
   - Speed selection (Slow/Normal/Fast)
   - Scrubber slider
   - Frame counter

2. **Data Display:**
   - 12 frames (last 2 hours)
   - 10-minute intervals
   - Timestamp with "hours ago" label
   - Auto-refresh every 10 minutes

3. **Interactivity:**
   - Zoom/pan
   - Location markers with popups
   - Persistent radar overlay at all zoom levels

### API Endpoints Used

```javascript
// Get radar frame list (for animation)
GET /api/radar/images
// Returns: {images: [{timestamp, filename}]}

// Get alerts (auto-refresh every 30s)
GET /api/alerts
// Returns: {alerts: [{id, message, created_at}]}

// Add location
POST /api/locations
// Body: {address: "Tel Aviv, Israel"}

// Dismiss alert
POST /api/alerts/{id}/dismiss

// Remove location
DELETE /api/locations/{id}

// Test alert
POST /api/test-alert
```

### Modal Dialogs

All dialogs use Windows 98 style:

1. **Add Location Dialog:** Inline on main page (no separate route)
2. **Confirmation Dialogs:** Location removal, etc.
3. **Alert/Success Messages:** Result feedback

---

## Database Schema

### Tables

#### `locations`
```sql
id              INTEGER PRIMARY KEY
address         TEXT NOT NULL        -- Geocoded address
latitude        FLOAT NOT NULL       -- Decimal degrees
longitude       FLOAT NOT NULL       -- Decimal degrees
active          BOOLEAN DEFAULT 1    -- Soft delete flag
created_at      TIMESTAMP
```

#### `alerts`
```sql
id              INTEGER PRIMARY KEY
location_id     INTEGER NOT NULL     -- FK to locations
alert_time      TIMESTAMP           -- When alert was created
rain_expected_at TIMESTAMP          -- When rain expected (usually alert_time + 5 min)
minutes_ahead   INTEGER             -- Always 10 for current system
message         TEXT                -- Alert message with location/intensity
dismissed       BOOLEAN DEFAULT 0   -- User dismissed flag
created_at      TIMESTAMP
```

#### `notification_settings`
```sql
id                      INTEGER PRIMARY KEY
email_enabled           BOOLEAN DEFAULT 0
email_address           TEXT
email_smtp_server       TEXT
email_smtp_port         INTEGER
email_smtp_user         TEXT
email_smtp_password     TEXT
slack_enabled           BOOLEAN DEFAULT 0
slack_webhook_url       TEXT
telegram_enabled        BOOLEAN DEFAULT 0
telegram_bot_token      TEXT
telegram_chat_id        TEXT
```

**Note:** Only one settings row exists (singleton pattern).

---

## Configuration

### Environment Variables (Optional)

```bash
FLASK_HOST=127.0.0.1  # Default
FLASK_PORT=5000       # Default
```

### Database Location

```python
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'data', 'rain_alert.db')
```

**Path:** `/Users/yoav.magor/dev/rain-alert/data/rain_alert.db`

### Radar Images Directory

```python
radar_dir = os.path.join(basedir, 'data', 'radar')
```

**Path:** `/Users/yoav.magor/dev/rain-alert/data/radar/`

### Scheduler Configuration

```python
# Check interval
IntervalTrigger(minutes=10)

# Alert deduplication window
timedelta(minutes=15)

# Runs on startup + every 10 minutes
```

---

## Known Limitations

### 1. RainViewer Coverage

- **Coverage:** Global but with gaps in some remote areas
- **Resolution:** ~1-2 km at zoom 7
- **Update Frequency:** ~10 minutes (depends on radar source)
- **Historical Data:** Only provides ~2 hours of past data

### 2. Detection Limitations

- **Point Detection:** Only checks exact location coordinates, not surrounding area
- **No Prediction:** Only detects current rain, no forecasting
- **Latency:** Up to 10-minute delay (scheduler interval + radar update)
- **Threshold:** Any alpha > 0 triggers alert (very sensitive)

### 3. Frontend Limitations

- **Radar Display:** Only shows Israel-centered tiles (could be expanded)
- **Animation:** Limited to available frames (~12 max)
- **No Mobile UI:** Desktop-optimized only

### 4. Notification Limitations

- **Rate Limiting:** 15-minute deduplication per location
- **No Aggregation:** Separate alert per location
- **No Escalation:** Single notification per detection

---

## Critical Design Decisions

### ✅ DO:

1. **Use GlobalRadarService for ALL alert detection**
   - Works globally
   - Real-time radar data
   - Simple pixel-based detection

2. **Keep scheduler at 10-minute intervals**
   - Matches RainViewer update frequency
   - Reasonable trade-off between responsiveness and API load

3. **Use RainViewer tile server directly**
   - No need to download/store all tiles
   - On-demand fetching per location
   - Always up-to-date

4. **Maintain 15-minute deduplication window**
   - Prevents alert spam
   - Reasonable for rain events

5. **Use SQLite with soft deletes**
   - Simple deployment
   - No complex migrations
   - Easy backup

### ❌ DO NOT:

1. **DO NOT use weather forecast APIs (Open-Meteo, etc.)**
   - Forecasts are not accurate enough for nowcasting
   - User explicitly rejected this approach

2. **DO NOT use RadarService for alert detection**
   - Only downloads Israel tiles
   - Only for frontend display
   - Not global coverage

3. **DO NOT use radar_nowcast.py**
   - Legacy code for Israel-only detection
   - Replaced by GlobalRadarService

4. **DO NOT download all radar tiles**
   - Waste of bandwidth/storage
   - Unnecessary with on-demand fetching

5. **DO NOT implement "zoom level not supported" messages**
   - Use errorTileUrl: '' to hide errors
   - Set maxNativeZoom correctly
   - Let Leaflet scale tiles beyond native zoom

6. **DO NOT create separate pages for actions**
   - Use modal dialogs instead
   - Keep everything on main page

---

## File Structure

```
rain-alert/
├── app/
│   ├── __init__.py              # Flask app factory, scheduler startup
│   ├── routes.py                # Web routes & API endpoints
│   ├── models.py                # SQLAlchemy models (Location, Alert, NotificationSettings)
│   ├── scheduler.py             # APScheduler jobs (check_all_locations, fetch_radar_images)
│   ├── radar_global.py          # ⭐ GlobalRadarService (CURRENT - use this)
│   ├── notifications.py         # NotificationService (Slack, Telegram, Email)
│   ├── weather.py               # WeatherService (geocoding only)
│   ├── radar.py                 # RadarService (Israel tiles for frontend only)
│   ├── radar_nowcast.py         # ❌ LEGACY - do not use
│   ├── static/
│   │   └── css/
│   │       └── windows98.css    # Windows 98 UI theme
│   └── templates/
│       ├── base.html            # Base template with Windows 98 window chrome
│       ├── index.html           # ⭐ Main page (map, alerts, locations)
│       └── settings.html        # Notification settings page
├── data/
│   ├── rain_alert.db            # SQLite database
│   └── radar/                   # Pre-fetched Israel radar PNGs (for UI only)
├── run.py                       # Development server entry point
├── requirements.txt             # Python dependencies
├── DESIGN.md                    # Original design document
└── TECHNICAL_OVERVIEW.md        # ⭐ This document

Key Files for Extension:
- app/radar_global.py    → Rain detection logic
- app/scheduler.py       → Background jobs
- app/routes.py          → API endpoints
- app/templates/index.html → Frontend UI
```

---

## Adding New Features - Guidelines

### Before Making Changes:

1. **Read this document thoroughly**
2. **Understand the GlobalRadarService architecture**
3. **Test changes with real locations that have current precipitation**
4. **Do not break existing alert detection**

### Common Extension Points:

#### 1. Add New Notification Channels

- Extend `NotificationService` in `app/notifications.py`
- Add settings fields to `NotificationSettings` model
- Update settings UI in `templates/settings.html`

#### 2. Enhance Detection Algorithms

- Modify `GlobalRadarService.check_rain_at_location()`
- Consider surrounding area, not just point
- Add velocity/direction tracking
- Improve intensity thresholds

#### 3. Add Location Features

- Location groups/categories
- Priority levels
- Custom alert thresholds per location
- Location search/autocomplete

#### 4. Improve Frontend

- Mobile responsive UI
- Multiple theme options
- Detailed radar analysis
- Historical alert logs

#### 5. Advanced Alerting

- Multi-level escalation
- Quiet hours
- Alert aggregation
- Webhook support

### Testing Checklist:

- ✅ Alerts still trigger for locations with current rain
- ✅ No false positives for clear weather locations
- ✅ Notifications sent to all configured channels
- ✅ UI displays radar overlay at all zoom levels
- ✅ No "zoom level not supported" messages
- ✅ Scheduler runs every 10 minutes
- ✅ Deduplication prevents spam

---

## Quick Reference Commands

### Start Server
```bash
source .venv/bin/activate
python run.py
```

### Check Logs
```bash
# See scheduler activity
tail -f /tmp/claude/-Users-yoav-magor-dev-rain-alert/tasks/*.output

# See what locations are being checked
grep "Checking:" /tmp/claude/-Users-yoav-magor-dev-rain-alert/tasks/*.output

# See rain detections
grep "Rain detected" /tmp/claude/-Users-yoav-magor-dev-rain-alert/tasks/*.output
```

### Database Queries
```bash
sqlite3 data/rain_alert.db "SELECT * FROM locations WHERE active=1;"
sqlite3 data/rain_alert.db "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 5;"
sqlite3 data/rain_alert.db "SELECT * FROM notification_settings;"
```

### Test Alert System
```bash
# Via UI: Click "🧪 Create Test Alert" button
# Via API:
curl -X POST http://localhost:5000/api/test-alert
```

---

## Success Metrics (Current Working State)

- ✅ Global rain detection working (tested with Turkey locations)
- ✅ Slack notifications received successfully
- ✅ Radar overlay displays correctly at all zoom levels
- ✅ No "zoom level not supported" errors
- ✅ Scheduler running every 10 minutes
- ✅ Alert deduplication working
- ✅ Windows 98 UI theme fully implemented
- ✅ Modal dialogs for all user actions
- ✅ Real-time alert updates (30s polling)

**Last Verified:** 2026-03-23 20:16 IST

---

## Contact & Support

**User:** yoav.magor
**Project Path:** `/Users/yoav.magor/dev/rain-alert`
**Database:** SQLite at `data/rain_alert.db`
**Server:** http://127.0.0.1:5000

---

## Version History

- **1.0 (2026-03-23):** Initial working version with global radar detection
  - Replaced Israel-only nowcasting with global tile-based detection
  - Fixed map zoom issues
  - Implemented modal dialogs
  - Verified alerts working for Turkish locations
