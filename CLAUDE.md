# Rain Alert: project guide

Single source of truth for architecture, status, and pitfalls. Usage is in README.md.
Don't add more handoff docs; update this file instead.
Work is tracked in GitHub issues on `tnm-claude/rain-alert` (public repo, so **no secrets**),
under milestone "Winter 2026-27" with epic #15.

## Runtime

- Host `giedi-prime` (MacBook Pro, macOS 12). LaunchAgent `com.rainalert.server`, installed from
  `deploy/` by `scripts/install-service.sh`. It runs `.venv/bin/python run.py` on `127.0.0.1:58101`.
- `run.py` sends all `print()`, stderr, and logging output to `logs/rain-alert.log` (rotating,
  timestamped). Flask debug is off.
- Secrets live in `.env` (gitignored, chmod 600), loaded by `app/config.py`. Env values override
  the `notification_settings` DB row. Email credentials are still DB-only.
- Scheduler jobs:
  - `check_weather` every 5 min
  - `fetch_radar` every 5 min (RainViewer fallback buffer plus daily pruning)
  - `poll_telegram` every 15 s (feedback buttons)
  - The first runs happen 15–30 s after startup.

## Architecture

| Module | Role |
|---|---|
| `app/detection.py` | Pure logic, no network: palette→dBZ, distance/bearing grid, cell filters, approach/ETA, alert decision, rain-event suppression, message text. **All thresholds are at the top.** |
| `app/radar_global.py` | Fetches RainViewer z7 tiles once per frame into a ~60 km mosaic (cached). `analyze_location()` always returns diagnostics; `check_rain_at_location()` returns the dict or None. |
| `app/scheduler.py` | Check loop: analyze → `capture.log_check` → (new event?) create Alert → `capture.snapshot_alert` → `NotificationService.send_alert(image_path=preview, diagnostics)` |
| `app/capture.py` | Pre-alert snapshot into `data/alerts/<id>/`, 500 MB prune, `detection_checks` log (180 days) |
| `app/notifications.py` | Slack Block Kit webhook; Telegram sendPhoto with ✅/❌ buttons and a getUpdates poller (owner chat only) |
| `app/ims_radar.py` | IMS radar frame list and image proxy for the UI (`/api/radar/ims`) |
| `app/health.py` | `/health` |
| `app/radar.py` | RainViewer rolling buffer (≤ 2 h, UTC names), used as a capture fallback |

Data:
- `data/rain_alert.db` has the tables `locations`, `alerts` (`user_feedback` is the label),
  `notification_settings`, and `detection_checks`.
- `data/alerts/<id>/` holds `ims_*`, `rv_*`, `rv_raw_*` (decodable), `w2d_*`, `preview.png`, and
  `detection.json`.

## Data sources (audited 2026-10-07)

- **RainViewer (primary detection input).**
  - Free API: 13 past frames (2 h at 10-min steps), **no nowcast, max zoom 7**, only colour
    scheme 2, 100 requests/min per IP.
  - Request `/256/7/{x}/{y}/2/0_0.png` (unsmoothed), so pixels are exact palette colours that
    decode to dBZ.
  - It covers central Israel (IMS Beit Dagan radar, ~9 km from Ramat Gan). The data is
    **10–20 min old** at check time, and the ETA compensates for that.
- **IMS (Israel Meteorological Service).**
  - Frame list: `https://ims.gov.il/he/radar_satellite/1` (`/he/` required),
    `data.types.IMSRadar[]`, at 5-min steps.
  - Images: `.../ims_data/map_images/IMSRadar4GIS/IMSRadar4GIS_<YYYYMMDDHHMM>_0.png`. These are
    940 px, transparent, and Web-Mercator stretched over lat 29.4469–34.5319, lon 31.7663–37.8648.
  - **`forecast_time` is Israel local time** (convert with `Asia/Jerusalem`, not a fixed +3).
  - Fresher than RainViewer (5–10 min). Used for the UI and capture now; the candidate to become
    the primary input is tracked in #21.
- **weather2day `radar.php`:** a 302 to `pics/radar/<epoch>.png`. It is the IMS picture on a
  terrain map with no georeferencing, so it is **display only**.
- **Not for detection:** Open-Meteo or any forecast API. That was tried in 2026-03 and rejected;
  forecasts can't nowcast. Open-Meteo is used only for geocoding.

## Status: winter 2026-27 revival (2026-10-07)

Done, as PRs to `main` (merge **#1 first**; every task branch is based on it):

| PR | Issue | Change |
|---|---|---|
| #1 | — | last season's false-positive fix (baseline) |
| #17 | #8 (#4, #5) | launchd autostart, rotating logs, `/health`, launchctl scripts |
| #22 | #9 (#3, #7) | detection rewrite: dBZ decoding, mosaic fetch, approach/ETA, event suppression |
| #20 | #10 (#2) | 60-min pre-alert capture, 500 MB cap, `detection_checks` |
| #16 | #11 (#19) | `.env` secrets, Slack Block Kit, Telegram photo + feedback buttons |
| #18 | #12 (#6) | UI: IMS radar tab, fixed RainViewer marker, removed broken iframes |
| this | #13 | docs consolidation |

The `integration/winter-2026` branch contains all of the above plus wiring commits. It has
83 passing tests and an end-to-end live run (Troodos test location): alert created, 468 KB
snapshot, Slack sent, second check suppressed.

Open:
- [ ] Owner merges the PRs. The auto-mode classifier blocks Claude from merging to main.
- [ ] Owner runs `scripts/install-service.sh install`. The classifier blocks Claude from doing the production install.
- [ ] Owner creates the Telegram bot and runs `scripts/telegram_setup.py`.
- [ ] First rainy day: check the IMS overlay alignment in the UI and in a capture.
- [ ] After ~10 labelled alerts: tune `app/detection.py` thresholds from `detection_checks`.
- [ ] #21: evaluate IMS as the primary input. It is fresher, but its colour scale needs decoding.

## Tuning workflow

1. Tap ✅/❌ on Telegram alerts. Labelled alerts are also protected from pruning.
2. Review with `/review-alerts` or `/review`, which shows frames, the preview, and `detection.json`.
3. Query `detection_checks` for misses: rain arrived (from logs or a label) but `should_alert=0`.
   Check `reason`, `distance_km`, `max_dbz`, `velocity_kmh`, `eta_minutes`, and `details` (JSON).
4. Change constants in `app/detection.py`. The tests in `tests/test_detection.py` pin the behaviour.

## Pitfalls (learned the hard way)

- **UTC everywhere.** Last season saved **0** pre-alert images because radar filenames were local
  time and `created_at` was UTC. IMS `forecast_time` is local, IMS `created` is UTC, and the
  weather2day epoch is UTC while its burned-in label is local.
- **HTTP 200 ≠ data.**
  - RainViewer z8+ and CARTO basemaps return 200 placeholder images.
  - Check tile mode and content (RainViewer tiles must be RGBA), not just the status code.
- **RainViewer alpha is not intensity.** Every pixel ≥ 15 dBZ has alpha 255, so the old
  `alpha > 120` test meant "any drizzle or clutter". Decode the palette instead.
- **Don't refetch tiles per sample.** The old code made ~144 requests per check; there is a
  100/min limit.
- **No stdout under nohup.** Block buffering hid every diagnostic. Always run via launchd and
  `run.py` (`PYTHONUNBUFFERED`, rotating log). Keep APScheduler at WARNING; DEBUG made a 32 MB log.
- **Only one `/health` route.** Flask asserts on duplicate endpoints and launchd then crash-loops.
- **Only one Telegram poller.** Two app instances, or a webhook set on the bot, cause `getUpdates` 409s.
- **FileVault is on, with no auto-login.**
  - After a reboot someone must type the disk password. The LaunchAgent then starts with the login.
  - Use `sudo fdesetup authrestart` for planned restarts.
  - On battery the machine sleeps after 15 min, so keep it on AC.
- **`db.create_all()` never adds columns** to existing tables. Prefer new tables or write an
  idempotent migration.
- **Agent worktrees** live under `.claude/worktrees/` (gitignored). Never `git add -A` in the
  main checkout while it has untracked `.env` or worktrees.

## Owner preferences

Windows 98 UI with modal dialogs, not separate pages. Minimal and no over-engineering. Real radar
only, never forecasts, for detection. Keep the map zoomable (scale tiles rather than capping zoom).
