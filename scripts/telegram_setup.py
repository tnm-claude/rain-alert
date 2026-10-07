#!/usr/bin/env python
"""
Set up the Telegram bot for rain alerts.

    .venv/bin/python scripts/telegram_setup.py [TOKEN]   # find chat_id, write .env, send a test
    .venv/bin/python scripts/telegram_setup.py --test    # send a sample alert with feedback buttons

TOKEN comes from the argument, TELEGRAM_BOT_TOKEN in .env, or a hidden prompt.
(Prefer the prompt/.env: arguments end up in shell history.)
The bot token and chat id are saved to .env (chmod 600) and never printed.
"""
import argparse
import getpass
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config
from app.notifications import NotificationService, build_message

WAIT_SECONDS = 180


def find_chat_id(token: str, bot_username: str):
    """Poll getUpdates until someone messages the bot; return (chat_id, first_name)."""
    print(f"\nOpen Telegram, search for @{bot_username}, press Start and send it any message "
          f"(waiting up to {WAIT_SECONDS // 60} min)...")
    deadline = time.time() + WAIT_SECONDS
    offset = None
    while time.time() < deadline:
        params = {'timeout': 20}
        if offset is not None:
            params['offset'] = offset
        data = NotificationService._tg(token, 'getUpdates', timeout=30, json=params)
        if data is None:
            print("getUpdates failed (see above). If it says 409, the bot has a webhook or another poller "
                  "is running (stop the rain-alert app and retry).")
            return None, None
        for update in data.get('result', []):
            offset = update['update_id'] + 1
            chat = (update.get('message') or {}).get('chat') or {}
            if chat.get('type') == 'private':
                NotificationService._tg(token, 'getUpdates', json={'offset': offset, 'timeout': 0})  # confirm
                return str(chat['id']), chat.get('first_name', '')
    return None, None


def send_sample(token: str, chat_id: str) -> bool:
    msg = build_message('', None, {
        'minutes_until_rain': 20, 'direction': 'SW', 'current_distance_km': 25,
        'max_dbz': 38, 'velocity_kmh': 45, 'approaching': True,
    }, config.env('PUBLIC_BASE_URL'))
    text = '[test] ' + msg['text'] + '\n\n(sample alert - the buttons below are real, nothing is saved)'
    return NotificationService.send_telegram(token, chat_id, text, alert_id=0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('token', nargs='?', help='bot token from @BotFather (default: .env or prompt)')
    parser.add_argument('--test', action='store_true', help='send a sample alert with feedback buttons')
    args = parser.parse_args()

    config.load_env()
    token = args.token or config.env('TELEGRAM_BOT_TOKEN')
    if not token and not args.test:
        token = getpass.getpass('Bot token from @BotFather (input hidden): ').strip()
    chat_id = config.env('TELEGRAM_CHAT_ID')

    if args.test:
        if not (token and chat_id):
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env - run without --test first.")
            return 1
        ok = send_sample(token, chat_id)
        print("Sample alert sent. Tap a button; with the app running the message answers 'Buttons work'."
              if ok else "Sending failed (see above).")
        return 0 if ok else 1

    if not token:
        print("No token given.")
        return 1
    me = NotificationService._tg(token, 'getMe')
    if me is None:
        print("getMe failed - check the token.")
        return 1
    username = me['result'].get('username', '?')
    print(f"Bot OK: @{username}")

    if args.token or not chat_id:
        chat_id, name = find_chat_id(token, username)
        if not chat_id:
            print("No message received. Nothing saved.")
            return 1
        print(f"Got a message from {name or 'you'} (chat id found).")
    else:
        print("Using TELEGRAM_CHAT_ID from .env (pass the token as an argument to re-discover it).")

    config.set_env_key('TELEGRAM_BOT_TOKEN', token)
    config.set_env_key('TELEGRAM_CHAT_ID', chat_id)
    print(f"Saved TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to {config.ENV_PATH} (chmod 600).")

    ok = NotificationService.send_telegram(token, chat_id, '[test] rain-alert is connected. Alerts will arrive here with ✅/❌ buttons.')
    print("Test message sent." if ok else "Test message failed (see above).")
    print("Restart the rain-alert app so it picks up the new .env (./restart.sh).")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
