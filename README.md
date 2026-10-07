# Rain Alert 🌧️

Self-hosted nowcasting for Israel: watches live radar and pings Slack/Telegram when rain is
moving towards a monitored location, before it arrives. Flask + APScheduler + SQLite, Windows 98 UI.

## Quick start

```bash
uv venv && uv pip install -r requirements.txt     # once (.venv, Python 3.12+)
cp .env.example .env && chmod 600 .env             # fill in Slack / Telegram
./scripts/install-service.sh install               # launchd: starts at login, restarts on crash
open http://127.0.0.1:58101
```

| Command | What it does |
|---|---|
| `./start.sh` / `./stop.sh` / `./restart.sh` / `./status.sh` | launchctl wrappers (`stop` stays stopped until `start` or next login) |
| `curl localhost:58101/health` | last check / radar fetch / uptime; HTTP 503 if no check in 15 min |
| `tail -f logs/rain-alert.log` | rotating log (2 MB × 6) |
| `.venv/bin/python -m pytest` | test suite (no network) |

## Notifications

- **Slack:** set `SLACK_WEBHOOK_URL` in `.env`.
- **Telegram:** create a bot with @BotFather (`/newbot`), then run
  `.venv/bin/python scripts/telegram_setup.py`. It asks for the token, waits for you to message
  the bot, and writes `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` to `.env`.
  `scripts/telegram_setup.py --test` sends a sample alert. Run `./restart.sh` afterwards.
- Telegram alerts carry a radar preview and ✅ Rain came / ❌ False alarm buttons. **Tap them.**
  That feedback is what the thresholds get tuned from.

## Tuning

Every alert saves the 60 minutes of radar before it in `data/alerts/<id>/`, under a 500 MB cap
that prunes unlabelled alerts first. Every check, alert or not, is logged to the
`detection_checks` table. Review alerts at `/review` or with the `/review-alerts` Claude command.
The thresholds live at the top of `app/detection.py`.

See [CLAUDE.md](CLAUDE.md) for architecture, data sources, status, and pitfalls.
