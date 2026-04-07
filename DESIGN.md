# Rain Alert - System Design

**Last Updated:** 2026-03-23
**Status:** Production Ready

---

## Purpose

Monitor specific geographic locations worldwide and send immediate alerts when rain is detected using real-time radar data.

---

## Core Features

1. **Global Rain Detection**
   - Real-time radar-based detection
   - Works anywhere with RainViewer coverage
   - 10-minute check intervals
   - Pixel-level accuracy

2. **Location Management**
   - Add unlimited locations worldwide
   - Geocoding via Nominatim (OSM)
   - Soft delete (preserves history)
   - Location markers on map

3. **Interactive Radar Map**
   - Leaflet.js with CartoDB basemap
   - 2-hour animated playback
   - Play/pause/speed controls
   - Full zoom support (5-15)
   - Global coverage

4. **Multi-Channel Notifications**
   - Slack webhooks
   - Telegram bot API
   - Email (SMTP)
   - Configurable per user

5. **Windows 98 UI Theme**
   - Retro design
   - Modal dialogs
   - Single-page interface
   - No authentication

---

## Technology Stack

### Backend
- **Flask 3.0:** Web framework
- **SQLAlchemy:** ORM
- **SQLite:** Database
- **APScheduler:** Background jobs
- **Pillow & NumPy:** Radar image processing

### Frontend
- **Leaflet.js 1.9.4:** Interactive mapping
- **Vanilla JavaScript:** No frameworks
- **Windows 98 CSS:** Custom retro theme

### Data Sources
- **RainViewer API:** Global radar tiles (FREE, no key required)
- **Nominatim/OSM:** Geocoding (FREE, no key required)

---

## Database Schema

### locations
```sql
id              INTEGER PRIMARY KEY
address         TEXT NOT NULL        -- Geocoded address
latitude        FLOAT NOT NULL       -- Decimal degrees
longitude       FLOAT NOT NULL       -- Decimal degrees
active          BOOLEAN DEFAULT 1    -- Soft delete
created_at      TIMESTAMP
```

### alerts
```sql
id              INTEGER PRIMARY KEY
location_id     INTEGER NOT NULL     -- FK to locations
alert_time      TIMESTAMP           -- When alert created
rain_expected_at TIMESTAMP          -- alert_time + 5 minutes
minutes_ahead   INTEGER             -- Always 10
message         TEXT                -- Full alert message
dismissed       BOOLEAN DEFAULT 0   -- User dismissed
created_at      TIMESTAMP
```

### notification_settings
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

**Note:** Only one settings row (singleton pattern).

---

## API Endpoints

### Web Routes
- `GET /` - Main page (map, alerts, locations)
- `GET /settings` - Notification configuration

### REST API
- `POST /api/locations` - Add location
  ```json
  {"address": "Tel Aviv, Israel"}
  ```

- `DELETE /api/locations/<id>` - Remove location

- `GET /api/alerts` - Get active alerts

- `POST /api/alerts/<id>/dismiss` - Dismiss alert

- `POST /api/test-alert` - Create test alert

- `GET /api/radar/images` - Get radar frame list (for UI animation)

- `GET /api/settings` - Get notification settings

- `POST /api/settings` - Update notification settings

- `POST /api/test-notifications` - Send test notification

---

## Rain Detection Algorithm

### Data Source: RainViewer API

**API:** `https://api.rainviewer.com/public/weather-maps.json`

Returns:
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

### Detection Process

For each monitored location:

1. **Fetch Latest Radar Frame**
   ```python
   response = requests.get('https://api.rainviewer.com/public/weather-maps.json')
   latest_frame = data['radar']['past'][-1]
   ```

2. **Calculate Tile Coordinates**
   ```python
   zoom = 7
   x_tile = int((lon + 180) / 360 * 2**zoom)
   y_tile = int((1 - asinh(tan(lat_rad)) / pi) / 2 * 2**zoom)
   ```

3. **Fetch Radar Tile**
   ```python
   tile_url = f"{host}{path}/256/{zoom}/{x_tile}/{y_tile}/2/1_0.png"
   img = Image.open(requests.get(tile_url).content)
   ```

4. **Calculate Pixel Coordinates**
   ```python
   pixel_x = int((x_tile_float - x_tile) * 256)
   pixel_y = int((y_tile_float - y_tile) * 256)
   ```

5. **Read Pixel RGBA**
   ```python
   r, g, b, a = img.getpixel((pixel_x, pixel_y))
   ```

6. **Detect Rain**
   ```python
   if a > 0:  # Alpha channel indicates precipitation
       intensity = a  # 0-255 scale
       confidence = 'high' if a > 100 else 'medium'
       return rain_detected(intensity, confidence)
   ```

### Why This Works

- **Real-time:** Uses actual radar data, not forecasts
- **Accurate:** Pixel-level precision at exact coordinates
- **Global:** Works anywhere RainViewer has coverage
- **Simple:** Single API call per location
- **Reliable:** Alpha channel directly indicates rain presence

---

## Scheduler

### Jobs

1. **check_all_locations** - Every 10 minutes
   - Queries all active locations
   - Calls GlobalRadarService for each
   - Creates alerts when rain detected
   - Sends notifications

2. **fetch_radar_images** - Every 10 minutes
   - Pre-fetches Israel radar tiles for UI display
   - Stores in `data/radar/` directory
   - Keeps 12 frames (2 hours)

### Configuration

```python
IntervalTrigger(minutes=10)  # Check interval
timedelta(minutes=15)        # Deduplication window
```

### Alert Deduplication

Prevents duplicate alerts within 15-minute window per location:

```python
existing = Alert.query.filter(
    Alert.location_id == location.id,
    Alert.minutes_ahead == 10,
    Alert.dismissed == False,
    Alert.created_at >= datetime.utcnow() - timedelta(minutes=15)
).first()

if not existing:
    create_alert()
```

---

## File Structure

```
rain-alert/
├── app/
│   ├── __init__.py              # Flask app factory + scheduler startup
│   ├── models.py                # SQLAlchemy models
│   ├── routes.py                # Web routes & API endpoints
│   ├── scheduler.py             # APScheduler jobs
│   ├── radar_global.py          # ⭐ Rain detection (GlobalRadarService)
│   ├── radar.py                 # UI radar images (Israel-only)
│   ├── weather.py               # Geocoding (Nominatim)
│   ├── notifications.py         # Multi-channel notifications
│   ├── static/
│   │   └── css/
│   │       └── windows98.css    # Windows 98 theme
│   └── templates/
│       ├── base.html            # Base template with window chrome
│       ├── index.html           # Main page (map, alerts, locations)
│       └── settings.html        # Notification configuration
├── data/
│   ├── rain_alert.db            # SQLite database
│   └── radar/                   # Pre-fetched radar PNGs
├── run.py                       # Development server entry point
├── requirements.txt             # Python dependencies
├── DESIGN.md                    # This file
├── TECHNICAL_OVERVIEW.md        # Detailed technical documentation
├── CONTEXT_FOR_NEW_SESSIONS.md  # Quick reference for AI assistants
└── WORKING_IMPLEMENTATION.md    # Code examples that work
```

---

## UI Design

### Main Page Layout

```
┌─────────────────────────────────────────────────────────────┐
│ Rain Alert - Home                                       [X] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─ Rain Radar - Last 2 Hours ─────────────────────────┐   │
│  │  [Interactive Leaflet Map with Radar Overlay]       │   │
│  │  - Location markers                                  │   │
│  │  - Zoom/pan controls                                 │   │
│  │  - Animated radar playback                           │   │
│  └─────────────────────────────────────────────────────┘   │
│  Timestamp: Mar 23, 15:20 (1h ago)                          │
│  [▶ Play] [◀ Prev] [Next ▶] Speed: [Normal ▼] ━━━●━━━     │
│                                                             │
│  ┌─ Active Alerts ──────────────────────────────────────┐  │
│  │  ⚠️ Moderate rain detected at Mersin, Turkey         │  │
│  │  [Dismiss]                                            │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ Monitored Locations ────────────────────────────────┐  │
│  │  • Ramat Gan, Israel     32.0830, 34.8096  [Remove]  │  │
│  │  • Mersin, Turkey        36.7978, 34.6298  [Remove]  │  │
│  │  • Konya, Turkey         37.8727, 32.4924  [Remove]  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  [+ Add New Location] [⚙️ Settings] [🧪 Create Test Alert] │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Modal Dialogs

All user interactions use Windows 98 style modal dialogs:

- Add location
- Confirm location removal
- Success/error messages
- Test alert confirmation

---

## Implementation Status

### ✅ Completed Features

- Global rain detection (RainViewer API)
- Interactive radar map with animation
- Multi-channel notifications (Slack, Telegram, Email)
- Location management with geocoding
- Alert system with deduplication
- Windows 98 UI theme
- Background scheduler
- Test alert functionality

### Current Metrics

- **Lines of Code:** ~600 (vs 18,000 in previous version)
- **Dependencies:** 8 packages
- **API Keys Required:** 0 (all free APIs)
- **Database Size:** <1 MB
- **Check Interval:** 10 minutes
- **Alert Latency:** <30 seconds (after detection)

### Verified Working

- ✅ Global rain detection (tested Turkey locations)
- ✅ Slack notifications sent successfully
- ✅ Radar overlay displays at all zoom levels
- ✅ No "zoom level not supported" errors
- ✅ Scheduler runs every 10 minutes
- ✅ Modal dialogs for all actions
- ✅ Location markers on map

---

## No Authentication

- Single-user application
- No login required
- Run locally (127.0.0.1:5000)
- OAuth can be added later if needed

---

## Configuration

### Environment Variables (Optional)

```bash
FLASK_HOST=127.0.0.1  # Default
FLASK_PORT=5000       # Default
```

### Database Location

```
/Users/yoav.magor/dev/rain-alert/data/rain_alert.db
```

### Notification Settings

Configured via Settings page UI:
- Slack webhook URL
- Telegram bot token + chat ID
- Email SMTP settings

---

## RainViewer API Details

### Coverage

- **Global:** Works worldwide where radar data available
- **Resolution:** ~1-2 km at zoom 7
- **Update Frequency:** ~10 minutes
- **Historical Data:** ~2 hours (12 frames)
- **Tile Format:** PNG with RGBA channels
- **Alpha Channel:** 0 = no rain, 1-255 = increasing intensity

### Tile Server

**Base URL:** `https://tilecache.rainviewer.com`

**Tile Format:** `{host}{path}/{size}/{zoom}/{x}/{y}/{color}/{smooth}_{snow}.png`

Parameters:
- `size`: 256 or 512 (pixels)
- `zoom`: 1-8 (tile zoom level)
- `x/y`: Tile coordinates
- `color`: 2 (default color scheme)
- `smooth`: 1 (smoothing enabled)
- `snow`: 0 (rain only, no snow)

### Rate Limits

- No authentication required
- No documented rate limits
- Reasonable use expected
- 10-minute check interval is conservative

---

## Success Criteria

- ✅ Can add locations anywhere in the world
- ✅ Locations get geocoded automatically
- ✅ Radar check runs every 10 minutes
- ✅ Alerts appear when rain detected
- ✅ Notifications sent to configured channels
- ✅ Can dismiss alerts
- ✅ Can remove locations
- ✅ Interactive map displays radar overlay
- ✅ UI looks like Windows 98
- ✅ No errors or crashes

---

## Future Enhancement Ideas

### Detection
- Surrounding area check (not just point)
- Rain velocity/direction tracking
- Multi-level intensity thresholds
- Predictive nowcasting (30-60 min ahead)

### Notifications
- SMS support (Twilio)
- Push notifications (mobile app)
- Webhook support
- Alert escalation
- Quiet hours

### UI
- Mobile responsive design
- Dark mode / alternate themes
- Historical alert log
- Location grouping
- Custom alert sounds

### Data
- Historical rain statistics
- Location-specific thresholds
- Alert accuracy tracking
- Export data (CSV/JSON)

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run server
python run.py

# Access UI
open http://127.0.0.1:5000

# Add location
Click "+ Add New Location" button
Enter address (e.g., "Tel Aviv, Israel")

# Configure notifications
Click "⚙️ Settings"
Enable and configure desired channels

# Test system
Click "🧪 Create Test Alert"
Check notifications received

# Wait for real alerts
Scheduler checks every 10 minutes
Alerts appear when rain detected
```

---

## Support

**User:** yoav.magor
**Project Path:** `/Users/yoav.magor/dev/rain-alert`
**Documentation:** See TECHNICAL_OVERVIEW.md for detailed specs

---

## Version History

- **1.0 (2026-03-23):** Initial production release
  - Global radar detection working
  - Multi-channel notifications
  - Interactive map with animation
  - Windows 98 UI complete
