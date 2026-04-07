# Working Implementation - DO NOT CHANGE

**This document contains the exact implementation details that are WORKING.**
**Do not "improve" or "optimize" these without explicit user request.**

---

## Rain Detection (GlobalRadarService)

### Location: `app/radar_global.py`

```python
@staticmethod
def check_rain_at_location(lat: float, lon: float) -> Optional[Dict]:
    """
    WORKING IMPLEMENTATION - Returns rain info or None
    """
    # 1. Fetch latest radar frame
    response = requests.get('https://api.rainviewer.com/public/weather-maps.json')
    data = response.json()
    latest_frame = data['radar']['past'][-1]
    host = data['host']
    path = latest_frame['path']

    # 2. Calculate tile coordinates
    zoom = 7
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)

    # 3. Fetch tile
    tile_url = f"{host}{path}/256/{zoom}/{x}/{y}/2/1_0.png"
    tile_response = requests.get(tile_url)
    img = Image.open(BytesIO(tile_response.content)).convert('RGBA')

    # 4. Calculate pixel within tile
    x_tile_float = (lon + 180.0) / 360.0 * n
    y_tile_float = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    pixel_x = int((x_tile_float - x) * 256)
    pixel_y = int((y_tile_float - y) * 256)

    # 5. Read pixel
    r, g, b, a = img.getpixel((pixel_x, pixel_y))

    # 6. If alpha > 0, rain detected
    if a > 0:
        return {
            'minutes_until_rain': 5,
            'expected_at': datetime.utcnow() + timedelta(minutes=5),
            'intensity': a,  # 0-255
            'confidence': 'high' if a > 100 else 'medium'
        }
    return None
```

**Why this works:**
- RainViewer uses alpha channel for precipitation intensity
- Tile coordinate math correctly maps lat/lon to tile grid
- Single HTTP request per location
- Works globally

**Do NOT:**
- Change zoom level from 7 (good balance of coverage vs detail)
- Use different tile size (256x256 is standard)
- Cache tiles aggressively (radar updates every 10 min)
- Add complex prediction algorithms (not needed)

---

## Scheduler Configuration

### Location: `app/scheduler.py`

```python
# WORKING INTERVAL - Do not change
scheduler.add_job(
    func=check_all_locations,
    trigger=IntervalTrigger(minutes=10),
    id='check_weather'
)

# WORKING DEDUPLICATION - Do not change
existing = Alert.query.filter(
    Alert.location_id == location.id,
    Alert.minutes_ahead == alert_threshold,
    Alert.dismissed == False,
    Alert.created_at >= datetime.utcnow() - timedelta(minutes=15)
).first()
```

**Why this works:**
- 10 minutes matches RainViewer update frequency
- 15-minute deduplication prevents spam
- Initial run on startup ensures immediate check

**Do NOT:**
- Reduce interval below 10 minutes (unnecessary API calls)
- Increase interval above 10 minutes (delays alerts)
- Change deduplication window (balanced for rain events)

---

## Frontend Map Configuration

### Location: `app/templates/index.html`

```javascript
// WORKING MAP CONFIG
map = L.map('radar-map', {
    center: [31.5, 34.8],
    zoom: 7,
    minZoom: 5,
    maxZoom: 15  // ← Full zoom range
});

// WORKING BASE LAYER
L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    attribution: '© OpenStreetMap contributors © CARTO',
    maxZoom: 19,
    subdomains: 'abcd'
});

// WORKING RADAR LAYER
radarLayer = L.tileLayer(tileUrl, {
    tileSize: 256,
    opacity: 0.6,
    zIndex: 10,
    minZoom: 0,
    maxZoom: 15,  // ← Allow display at all zoom levels
    maxNativeZoom: 8,  // ← RainViewer native tiles
    errorTileUrl: '',  // ← Empty = hide error tiles
    bounds: null  // ← Global coverage
});
```

**Why this works:**
- maxZoom: 15 allows users to zoom in fully
- maxNativeZoom: 8 tells Leaflet to scale tiles beyond zoom 8
- errorTileUrl: '' hides "not supported" messages
- CartoDB Voyager has clean styling

**Do NOT:**
- Limit maxZoom (restricts user)
- Remove errorTileUrl: '' (shows ugly errors)
- Change maxNativeZoom to match maxZoom (causes blank tiles)
- Switch back to OpenStreetMap (has tile issues)

---

## Alert Message Format

### Location: `app/scheduler.py`

```python
# WORKING MESSAGE FORMAT
intensity_label = "Heavy" if intensity > 150 else "Moderate" if intensity > 80 else "Light"
message = f"⚠️ {intensity_label} rain detected at {location.address} (radar-confirmed, {confidence} confidence)"
```

**Why this works:**
- Clear severity indication
- Shows confidence level
- Includes location name
- Emoji makes it visually distinct

**Do NOT:**
- Remove location name (user needs to know where)
- Remove confidence level (indicates data quality)
- Make messages too verbose (notification channels have limits)

---

## Database Models

### Location: `app/models.py`

```python
class Location(db.Model):
    __tablename__ = 'locations'
    id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(500), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, default=True)  # ← Soft delete
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
```

**Why this works:**
- Soft deletes preserve history
- Geocoded address stored for display
- Simple, no complex relationships

**Do NOT:**
- Use hard deletes (lose history)
- Add complex relationships without need
- Change column types (breaks existing data)

---

## Notification Service

### Location: `app/notifications.py`

```python
# WORKING SLACK IMPLEMENTATION
payload = {'text': message}
response = requests.post(webhook_url, json=payload, timeout=10)

# WORKING TELEGRAM IMPLEMENTATION
url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
payload = {'chat_id': chat_id, 'text': message}
response = requests.post(url, json=payload, timeout=10)

# WORKING EMAIL IMPLEMENTATION
with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
    server.starttls()
    server.login(smtp_user, smtp_password)
    server.send_message(msg)
```

**Why this works:**
- Simple POST requests with timeout
- At least one channel success = notification sent
- Error handling per channel

**Do NOT:**
- Remove timeouts (can hang indefinitely)
- Require all channels to succeed (one is enough)
- Add complex retry logic (10-minute scheduler will retry)

---

## Windows 98 UI Theme

### Location: `app/static/css/windows98.css`

**Current theme is WORKING and APPROVED by user.**

**Do NOT:**
- Switch to modern UI frameworks (Bootstrap, Material, Tailwind)
- Remove retro styling
- Change button styles
- Modify window chrome appearance
- Add animations/transitions (not period-appropriate)

---

## API Endpoints

### Location: `app/routes.py`

```python
# WORKING ADD LOCATION
@app.route('/api/locations', methods=['POST'])
def api_add_location():
    data = request.get_json()
    address = data.get('address', '').strip()

    # Geocode
    result = WeatherService.geocode_address(address)
    if not result:
        return jsonify({'error': 'Could not find this address'}), 404

    lat, lon, display_name = result

    # Check existing
    existing = Location.query.filter_by(latitude=lat, longitude=lon).first()
    if existing:
        if not existing.active:
            existing.active = True
            db.session.commit()
        return jsonify(existing.to_dict()), 200

    # Create new
    location = Location(address=display_name, latitude=lat, longitude=lon, active=True)
    db.session.add(location)
    db.session.commit()
    return jsonify(location.to_dict()), 201
```

**Why this works:**
- Geocodes address to coordinates
- Checks for duplicates
- Reactivates soft-deleted locations
- Returns consistent response format

**Do NOT:**
- Skip geocoding (need coordinates)
- Allow duplicate coordinates (clutters UI)
- Return 500 errors for user errors (use 400/404)

---

## Radar Image Pre-fetching (UI Only)

### Location: `app/radar.py`

```python
# This is ONLY for frontend display, NOT for alert detection
ISRAEL_LAT = 31.5
ISRAEL_LON = 34.8
ZOOM_LEVEL = 7

tile_url = f"{host}{path}/512/{ZOOM_LEVEL}/{ISRAEL_LAT}/{ISRAEL_LON}/2/1_0.png"
```

**Why this works:**
- Pre-fetches tiles for smooth UI animation
- Stores locally to reduce repeated API calls
- Only covers Israel (where user is located)

**Do NOT:**
- Use this for alert detection (use GlobalRadarService)
- Expand to cover all monitored locations (too much storage)
- Increase MAX_IMAGES beyond 12 (2 hours is enough)

---

## Critical Files - Change With Care

| File | Purpose | Change Risk |
|------|---------|-------------|
| `app/radar_global.py` | Rain detection | HIGH - breaks alerts |
| `app/scheduler.py` | Background jobs | HIGH - breaks automation |
| `app/templates/index.html` | Main UI | MEDIUM - breaks UX |
| `app/models.py` | Database schema | HIGH - breaks data |
| `app/notifications.py` | Alert delivery | MEDIUM - breaks notifications |
| `app/routes.py` | API endpoints | MEDIUM - breaks frontend |
| `app/static/css/windows98.css` | UI theme | LOW - visual only |

---

## Testing After Changes

1. **Restart server:** `lsof -ti:5000 | xargs kill -9; python run.py`
2. **Check scheduler runs:** Look for "Checking X locations" in logs
3. **Add test location:** Use location with current rain
4. **Wait 10 minutes:** Let scheduler detect rain
5. **Verify alert:** Check UI and notification channels
6. **Test map:** Zoom in/out, verify radar overlay persists

---

## Quick Verification

```bash
# Is scheduler running?
grep "Scheduler" /tmp/claude/-Users-yoav-magor-dev-rain-alert/tasks/*.output | tail -5

# Are alerts being created?
sqlite3 data/rain_alert.db "SELECT COUNT(*) FROM alerts WHERE created_at > datetime('now', '-1 hour');"

# Are notifications configured?
sqlite3 data/rain_alert.db "SELECT email_enabled, slack_enabled, telegram_enabled FROM notification_settings;"

# Is radar detection working?
grep "Rain detected" /tmp/claude/-Users-yoav-magor-dev-rain-alert/tasks/*.output | tail -5
```

---

**Last Verified Working:** 2026-03-23 20:16 IST
**Test Locations:** Mersin Turkey, Konya Turkey (both detected rain successfully)
**Notifications:** Slack confirmed working
**Map Display:** Radar overlay visible at all zoom levels (5-15)
