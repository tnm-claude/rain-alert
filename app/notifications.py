"""
Notification service for sending alerts via Slack, Telegram, and Email.

Secrets come from .env (see app/config.py) and override DB settings.
Telegram alerts carry inline feedback buttons; poll_telegram_updates() records
the taps into Alert.user_feedback (no public URL needed, uses getUpdates).
"""
import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace

import requests

from app import config
from app.models import Alert, NotificationSettings, db

OFFSET_FILE = os.path.join(config.BASEDIR, 'data', 'telegram_offset.json')

COMPASS = {
    'N': 'north', 'NNE': 'north-northeast', 'NE': 'northeast', 'ENE': 'east-northeast',
    'E': 'east', 'ESE': 'east-southeast', 'SE': 'southeast', 'SSE': 'south-southeast',
    'S': 'south', 'SSW': 'south-southwest', 'SW': 'southwest', 'WSW': 'west-southwest',
    'W': 'west', 'WNW': 'west-northwest', 'NW': 'northwest', 'NNW': 'north-northwest',
}

FEEDBACK_LABELS = {True: '✅ Recorded: rain came', False: '❌ Recorded: false alarm'}
FEEDBACK_MARK = '\n\n── '


class SendResults(dict):
    """Per-channel results ({'slack': True, ...}); truthy if any channel succeeded."""

    def __bool__(self):
        return any(self.values())


def _redact(text, secrets) -> str:
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, '***')
    return text


def short_location(address, parts: int = 2) -> str:
    """First `parts` comma-separated pieces of a (long) address."""
    if not address:
        return ''
    pieces = [p.strip() for p in str(address).split(',') if p.strip()]
    return ', '.join(pieces[:parts])


def intensity_label(diagnostics) -> str:
    """Human intensity text from max_dbz (preferred) or the legacy `intensity` value."""
    dbz = diagnostics.get('max_dbz')
    if isinstance(dbz, (int, float)):
        label = 'Light' if dbz < 30 else 'Moderate' if dbz < 40 else 'Heavy' if dbz < 50 else 'Very heavy'
        return f'{label} ({dbz:.0f} dBZ)'
    value = diagnostics.get('intensity')
    if isinstance(value, (int, float)):
        return 'Heavy' if value > 150 else 'Moderate' if value > 80 else 'Light'
    return ''


def build_message(message: str, alert=None, diagnostics=None, base_url: str = '') -> dict:
    """Structured alert text shared by all channels.

    Returns {'title', 'place', 'lines', 'link', 'text'}. Without diagnostics the
    caller's `message` is used as the title (backward compatible).
    """
    place = ''
    if alert is not None and getattr(alert, 'location', None) is not None:
        place = short_location(alert.location.address)
    link = f'{base_url.rstrip("/")}/review' if base_url else ''

    lines = []
    if diagnostics:
        d = diagnostics
        minutes = d.get('minutes_until_rain')
        if minutes == 0:
            title = '🌧️ Rain is at your location now'
        elif d.get('approaching') is False:
            title = '🌧️ Rain nearby'
        else:
            title = '🌧️ Rain is moving towards you'
        if isinstance(minutes, (int, float)) and minutes > 0:
            lines.append(f'⏱ ETA ~{minutes:.0f} min')
        direction, dist = d.get('direction'), d.get('current_distance_km')
        if direction and isinstance(dist, (int, float)):
            lines.append(f'🧭 From the {COMPASS.get(str(direction).upper(), direction)}, {dist:.0f} km away')
        elif direction:
            lines.append(f'🧭 From the {COMPASS.get(str(direction).upper(), direction)}')
        elif isinstance(dist, (int, float)):
            lines.append(f'📏 {dist:.0f} km away')
        strength = intensity_label(d)
        speed = d.get('velocity_kmh')
        if strength and isinstance(speed, (int, float)) and speed > 0:
            lines.append(f'💧 {strength}, moving {speed:.0f} km/h')
        elif strength:
            lines.append(f'💧 {strength}')
        elif isinstance(speed, (int, float)) and speed > 0:
            lines.append(f'💨 Moving {speed:.0f} km/h')
    else:
        title = message

    if place and place in title:  # legacy messages already name the location
        place = ''
    parts = [title]
    if place:
        parts.append(f'📍 {place}')
    parts += lines
    if link:
        parts.append(f'🔗 {link}')
    return {'title': title, 'place': place, 'lines': lines, 'link': link, 'text': '\n'.join(parts)}


def _slack_escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class NotificationService:
    """Service for sending notifications"""

    # ---------- configuration ----------

    @staticmethod
    def effective_config(settings=None):
        """Merge .env (wins) with DB settings into one namespace.

        A channel is enabled when its env vars are set, otherwise by the DB flag + credentials.
        """
        def db_value(name):
            return getattr(settings, name, None) if settings is not None else None

        slack_env = config.env('SLACK_WEBHOOK_URL')
        tg_token_env = config.env('TELEGRAM_BOT_TOKEN')
        tg_chat_env = config.env('TELEGRAM_CHAT_ID')

        slack_url = slack_env or db_value('slack_webhook_url') or ''
        slack_enabled = bool(slack_url) and (bool(slack_env) or bool(db_value('slack_enabled')))

        tg_token = tg_token_env or db_value('telegram_bot_token') or ''
        tg_chat = tg_chat_env or db_value('telegram_chat_id') or ''
        tg_enabled = bool(tg_token and tg_chat) and (
            bool(tg_token_env or tg_chat_env) or bool(db_value('telegram_enabled')))

        return SimpleNamespace(
            slack_enabled=slack_enabled, slack_url=slack_url,
            telegram_enabled=tg_enabled, telegram_token=tg_token, telegram_chat_id=str(tg_chat),
            base_url=config.env('PUBLIC_BASE_URL'),
            slack_from_env=bool(slack_env), telegram_from_env=bool(tg_token_env and tg_chat_env),
        )

    # ---------- Slack ----------

    @staticmethod
    def build_slack_payload(msg: dict) -> dict:
        title, place = _slack_escape(msg['title']), _slack_escape(msg['place'])
        head = f'*{title}*' + (f'\n📍 {place}' if place else '')
        blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': head}}]
        if msg['lines']:
            body = '\n'.join(_slack_escape(x) for x in msg['lines'])
            blocks.append({'type': 'section', 'text': {'type': 'mrkdwn', 'text': body}})
        if msg['link']:
            blocks.append({'type': 'context', 'elements': [
                {'type': 'mrkdwn', 'text': f'<{msg["link"]}|Open Rain Alert to give feedback>'}]})
        fallback = msg['title'] + (f' — {msg["place"]}' if msg['place'] else '')
        return {'text': fallback, 'blocks': blocks}

    @staticmethod
    def send_slack(webhook_url: str, message, alert_id: int | None = None, latitude: float | None = None,
                   longitude: float | None = None, address: str | None = None) -> bool:
        """Send to a Slack incoming webhook. `message` is a str or a build_message() dict.

        Webhooks cannot receive button clicks (needs a public URL), so there are no buttons.
        The old image block was dropped: Slack caches image URLs and radar.php is a generic,
        location-less map behind a redirect.
        """
        if isinstance(message, dict):
            payload = NotificationService.build_slack_payload(message)
        elif alert_id:
            payload = NotificationService.build_slack_payload(
                {'title': message, 'place': short_location(address), 'lines': [], 'link': ''})
        else:
            payload = {'text': message}
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
        except Exception as e:
            print(f"[Notifications] Slack error: {_redact(e, [webhook_url])}")
            return False
        if response.status_code != 200:
            print(f"[Notifications] Slack failed: HTTP {response.status_code} "
                  f"{_redact(response.text[:200], [webhook_url])}")
            return False
        return True

    # ---------- Telegram ----------

    @staticmethod
    def _tg(token: str, method: str, timeout: int = 10, **kwargs):
        """Call a Telegram Bot API method. Returns the parsed JSON or None; logs failures."""
        url = f"https://api.telegram.org/bot{token}/{method}"
        try:
            response = requests.post(url, timeout=timeout, **kwargs)
        except Exception as e:
            print(f"[Notifications] Telegram {method} error: {_redact(e, [token])}")
            return None
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code != 200 or not (data or {}).get('ok'):
            print(f"[Notifications] Telegram {method} failed: HTTP {response.status_code} "
                  f"{_redact(response.text[:200], [token])}")
            return None
        return data

    @staticmethod
    def feedback_keyboard(alert_id: int) -> dict:
        return {'inline_keyboard': [[
            {'text': '✅ Rain came', 'callback_data': f'fb:{alert_id}:1'},
            {'text': '❌ False alarm', 'callback_data': f'fb:{alert_id}:0'},
        ]]}

    @staticmethod
    def send_telegram(bot_token: str, chat_id: str, message: str, image_path: str | None = None,
                      alert_id: int | None = None) -> bool:
        """Send to Telegram: sendPhoto if image_path exists, else sendMessage.

        With alert_id, adds feedback buttons (alert_id 0 = test buttons, nothing saved).
        """
        keyboard = NotificationService.feedback_keyboard(alert_id) if alert_id is not None else None
        if image_path and os.path.isfile(image_path):
            data = {'chat_id': chat_id, 'caption': message[:1024]}
            if keyboard:
                data['reply_markup'] = json.dumps(keyboard)
            try:
                with open(image_path, 'rb') as f:
                    if NotificationService._tg(bot_token, 'sendPhoto', timeout=30, data=data, files={'photo': f}):
                        return True
            except OSError as e:
                print(f"[Notifications] Telegram image read error: {e}")
            print("[Notifications] Telegram photo failed, falling back to text")
        payload = {'chat_id': chat_id, 'text': message}
        if keyboard:
            payload['reply_markup'] = keyboard
        return NotificationService._tg(bot_token, 'sendMessage', json=payload) is not None

    @staticmethod
    def _load_offset(token: str):
        try:
            with open(OFFSET_FILE) as f:
                saved = json.load(f)
            if saved.get('bot') == token.split(':')[0]:
                return int(saved['offset'])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    @staticmethod
    def _save_offset(token: str, offset: int) -> None:
        try:
            os.makedirs(os.path.dirname(OFFSET_FILE), exist_ok=True)
            with open(OFFSET_FILE, 'w') as f:
                json.dump({'bot': token.split(':')[0], 'offset': offset}, f)
        except OSError as e:
            print(f"[Notifications] Could not save Telegram offset: {e}")

    @staticmethod
    def handle_telegram_update(update: dict, token: str, chat_id: str) -> bool:
        """Handle one getUpdates item (needs an app context). True if feedback was recorded.

        Only callbacks from messages in the configured chat are accepted.
        """
        cq = update.get('callback_query')
        if not cq:
            return False
        msg = cq.get('message') or {}
        if str((msg.get('chat') or {}).get('id', '')) != str(chat_id):
            print("[Notifications] Ignored Telegram callback from a foreign chat")
            return False

        def answer(text):
            NotificationService._tg(token, 'answerCallbackQuery',
                                    json={'callback_query_id': cq['id'], 'text': text})

        parts = (cq.get('data') or '').split(':')
        if len(parts) != 3 or parts[0] != 'fb' or parts[2] not in ('0', '1') or not parts[1].isdigit():
            answer('Unknown action')
            return False
        alert_id, verdict = int(parts[1]), parts[2] == '1'

        if alert_id == 0:  # test buttons from scripts/telegram_setup.py --test
            answer('Buttons work (test, nothing saved)')
            return False
        alert = db.session.get(Alert, alert_id)
        if alert is None:
            answer('Alert not found')
            return False
        alert.user_feedback = verdict
        alert.feedback_timestamp = datetime.utcnow()
        db.session.commit()
        print(f"[Notifications] Feedback for alert {alert_id}: {'rain came' if verdict else 'false alarm'}")

        label = FEEDBACK_LABELS[verdict]
        answer(label)
        has_photo = 'photo' in msg
        original = (msg.get('caption') if has_photo else msg.get('text')) or ''
        edited = original.split(FEEDBACK_MARK)[0] + FEEDBACK_MARK + label
        NotificationService._tg(token, 'editMessageCaption' if has_photo else 'editMessageText', json={
            'chat_id': chat_id, 'message_id': msg.get('message_id'),
            'caption' if has_photo else 'text': edited,
            'reply_markup': NotificationService.feedback_keyboard(alert_id),
        })
        return True

    @staticmethod
    def poll_telegram_updates(settings=None, timeout: int = 10) -> int:
        """Fetch pending Telegram updates once (long-poll up to `timeout` s) and process them.

        Needs an app context. Returns the number of feedback answers recorded.
        """
        if settings is None:
            settings = NotificationSettings.query.first()
        cfg = NotificationService.effective_config(settings)
        if not cfg.telegram_enabled:
            return 0
        params = {'timeout': timeout, 'allowed_updates': ['callback_query']}
        offset = NotificationService._load_offset(cfg.telegram_token)
        if offset is not None:
            params['offset'] = offset
        data = NotificationService._tg(cfg.telegram_token, 'getUpdates', timeout=timeout + 10, json=params)
        recorded = 0
        for update in (data or {}).get('result', []):
            try:
                if NotificationService.handle_telegram_update(update, cfg.telegram_token, cfg.telegram_chat_id):
                    recorded += 1
            except Exception as e:
                db.session.rollback()
                print(f"[Notifications] Error handling Telegram update: {_redact(e, [cfg.telegram_token])}")
            offset = update['update_id'] + 1
            NotificationService._save_offset(cfg.telegram_token, offset)
        return recorded

    # ---------- Email ----------

    @staticmethod
    def send_email(
        smtp_server: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        to_address: str,
        subject: str,
        message: str
    ) -> bool:
        """Send notification via email"""
        try:
            msg = MIMEMultipart()
            msg['From'] = smtp_user
            msg['To'] = to_address
            msg['Subject'] = subject
            msg.attach(MIMEText(message, 'plain'))

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)

            return True
        except Exception as e:
            print(f"[Notifications] Email error: {_redact(e, [smtp_password])}")
            return False

    # ---------- dispatch ----------

    @staticmethod
    def send_alert(settings, message: str, alert=None, image_path: str | None = None, diagnostics: dict | None = None):
        """Send an alert on every configured channel.

        Args:
            settings: NotificationSettings object (or None when everything comes from .env)
            message: fallback text (used as the title when no diagnostics are given)
            alert: optional Alert, adds the location name and Telegram feedback buttons
            image_path: optional radar image for Telegram (sendPhoto)
            diagnostics: optional detection dict (direction, current_distance_km,
                minutes_until_rain, max_dbz, velocity_kmh); missing keys are skipped

        Returns:
            SendResults, e.g. {'slack': True, 'telegram': False}; truthy if any channel succeeded.
        """
        cfg = NotificationService.effective_config(settings)
        msg = build_message(message, alert, diagnostics, cfg.base_url)
        alert_id = alert.id if alert is not None and getattr(alert, 'id', None) else None
        results = SendResults()

        if cfg.slack_enabled:
            results['slack'] = NotificationService.send_slack(cfg.slack_url, msg)
        if cfg.telegram_enabled:
            results['telegram'] = NotificationService.send_telegram(
                cfg.telegram_token, cfg.telegram_chat_id, msg['text'], image_path, alert_id)

        email_ok = settings is not None and settings.email_enabled and all([
            settings.email_address, settings.email_smtp_server, settings.email_smtp_port,
            settings.email_smtp_user, settings.email_smtp_password,
        ])
        if email_ok:
            results['email'] = NotificationService.send_email(
                settings.email_smtp_server, settings.email_smtp_port, settings.email_smtp_user,
                settings.email_smtp_password, settings.email_address, "Rain Alert", msg['text'])

        if not results:
            print("[Notifications] No notification methods configured")
        else:
            print(f"[Notifications] Results: {dict(results)}")
        return results
