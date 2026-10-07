"""Tests for notifications: formatting, env precedence, Telegram feedback handling (HTTP mocked)."""
import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app import config, notifications
from app.models import Alert, Location, NotificationSettings, db
from app.notifications import NotificationService, build_message, short_location

TOKEN = '123456:SECRET-TOKEN'
CHAT = '555'
HOOK = 'https://hooks.slack.com/services/T000/B000/SECRETSECRET'
ADDRESS = 'רחוב הרצל 1, תל אביב-יפו, מחוז תל אביב, ישראל'
FULL = {'minutes_until_rain': 20, 'direction': 'SW', 'current_distance_km': 24.6,
        'max_dbz': 38, 'velocity_kmh': 45, 'approaching': True}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in config.KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(notifications, 'OFFSET_FILE', str(tmp_path / 'offset.json'))


@pytest.fixture
def app_ctx():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def alert(app_ctx):
    loc = Location(address=ADDRESS, latitude=32.0, longitude=34.8)
    db.session.add(loc)
    db.session.commit()
    a = Alert(location_id=loc.id, rain_expected_at=datetime.utcnow(), minutes_ahead=20, message='x')
    db.session.add(a)
    db.session.commit()
    return a


def fake_post(monkeypatch, status=200, body=None):
    """Replace requests.post; returns the MagicMock (calls: url, kwargs)."""
    resp = MagicMock(status_code=status, text=json.dumps(body or {'ok': True}))
    resp.json.return_value = body or {'ok': True}
    mock = MagicMock(return_value=resp)
    monkeypatch.setattr(notifications.requests, 'post', mock)
    return mock


# ---------- formatting ----------

def test_short_location_keeps_two_parts():
    assert short_location(ADDRESS) == 'רחוב הרצל 1, תל אביב-יפו'
    assert short_location(None) == ''


def test_message_full_diagnostics(alert):
    msg = build_message('ignored', alert, FULL, 'http://box.ts.net:58101/')
    text = msg['text']
    assert text.startswith('🌧️ Rain is moving towards you')
    assert 'רחוב הרצל 1, תל אביב-יפו' in text and 'מחוז' not in text
    assert 'ETA ~20 min' in text
    assert 'From the southwest, 25 km away' in text
    assert 'Moderate (38 dBZ), moving 45 km/h' in text
    assert text.endswith('http://box.ts.net:58101/review')


def test_message_partial_diagnostics(alert):
    msg = build_message('ignored', alert, {'direction': 'N'})
    assert msg['text'].splitlines()[1].startswith('📍')
    assert 'From the north' in msg['text']
    assert 'ETA' not in msg['text'] and 'km/h' not in msg['text'] and '🔗' not in msg['text']
    # only distance
    assert '12 km away' in build_message('m', alert, {'current_distance_km': 12})['text']


def test_message_now_and_not_approaching(alert):
    assert 'at your location now' in build_message('m', alert, {'minutes_until_rain': 0})['title']
    assert build_message('m', alert, {'approaching': False, 'minutes_until_rain': 15})['title'] == '🌧️ Rain nearby'


def test_message_without_diagnostics_uses_caller_text(alert):
    msg = build_message('Rain soon', alert, None)
    assert msg['text'].splitlines() == ['Rain soon', '📍 רחוב הרצל 1, תל אביב-יפו']
    assert build_message('Rain at רחוב הרצל 1, תל אביב-יפו', alert, None)['text'].count('הרצל') == 1


def test_slack_payload_has_blocks_and_no_image(alert):
    payload = NotificationService.build_slack_payload(build_message('m', alert, FULL, 'http://x'))
    assert payload['text'] and all(b['type'] != 'image' for b in payload['blocks'])
    assert payload['blocks'][-1]['type'] == 'context'


# ---------- env vs DB ----------

def test_env_overrides_db(monkeypatch):
    s = NotificationSettings(slack_enabled=True, slack_webhook_url='https://db.example/hook',
                             telegram_enabled=True, telegram_bot_token='1:db', telegram_chat_id='1')
    cfg = NotificationService.effective_config(s)
    assert (cfg.slack_url, cfg.telegram_token, cfg.telegram_from_env) == ('https://db.example/hook', '1:db', False)

    monkeypatch.setenv('SLACK_WEBHOOK_URL', HOOK)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', TOKEN)
    monkeypatch.setenv('TELEGRAM_CHAT_ID', CHAT)
    cfg = NotificationService.effective_config(s)
    assert (cfg.slack_url, cfg.telegram_token, cfg.telegram_chat_id) == (HOOK, TOKEN, CHAT)


def test_env_enables_channel_without_db_row(monkeypatch):
    assert not NotificationService.effective_config(None).slack_enabled
    monkeypatch.setenv('SLACK_WEBHOOK_URL', HOOK)
    assert NotificationService.effective_config(None).slack_enabled
    # DB flag off but env set -> still enabled; DB flag off, no env -> disabled
    off = NotificationSettings(slack_enabled=False, slack_webhook_url='https://db.example/hook')
    assert NotificationService.effective_config(off).slack_enabled


def test_load_env_file(tmp_path, monkeypatch):
    f = tmp_path / '.env'
    f.write_text('# c\nSLACK_WEBHOOK_URL="https://a/b"\nexport TELEGRAM_CHAT_ID=42\nPUBLIC_BASE_URL=\n')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'real-env-wins')
    config.load_env(str(f))
    assert config.env('SLACK_WEBHOOK_URL') == 'https://a/b'
    assert config.env('TELEGRAM_CHAT_ID') == 'real-env-wins'
    assert config.env('PUBLIC_BASE_URL') == ''


# ---------- sending ----------

def test_send_alert_per_channel_results_and_backward_compat(monkeypatch, alert):
    monkeypatch.setenv('SLACK_WEBHOOK_URL', HOOK)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', TOKEN)
    monkeypatch.setenv('TELEGRAM_CHAT_ID', CHAT)
    mock = fake_post(monkeypatch)
    # old call style: positional (settings, message, alert)
    results = NotificationService.send_alert(NotificationSettings(), 'Rain!', alert)
    assert dict(results) == {'slack': True, 'telegram': True} and results
    urls = [c.args[0] for c in mock.call_args_list]
    assert HOOK in urls and f'https://api.telegram.org/bot{TOKEN}/sendMessage' in urls
    tg = next(c for c in mock.call_args_list if 'telegram' in c.args[0]).kwargs['json']
    assert tg['chat_id'] == CHAT
    buttons = tg['reply_markup']['inline_keyboard'][0]
    assert [b['callback_data'] for b in buttons] == [f'fb:{alert.id}:1', f'fb:{alert.id}:0']


def test_send_alert_uses_sendphoto_with_image(monkeypatch, alert, tmp_path):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', TOKEN)
    monkeypatch.setenv('TELEGRAM_CHAT_ID', CHAT)
    img = tmp_path / 'p.png'
    img.write_bytes(b'\x89PNG')
    mock = fake_post(monkeypatch)
    NotificationService.send_alert(None, 'm', alert, image_path=str(img), diagnostics=FULL)
    assert mock.call_args.args[0].endswith('/sendPhoto')
    assert 'ETA ~20 min' in mock.call_args.kwargs['data']['caption']
    assert 'reply_markup' in mock.call_args.kwargs['data']


def test_failure_logged_with_status_and_body_but_no_secret(monkeypatch, capsys):
    monkeypatch.setenv('SLACK_WEBHOOK_URL', HOOK)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', TOKEN)
    monkeypatch.setenv('TELEGRAM_CHAT_ID', CHAT)
    fake_post(monkeypatch, status=400, body={'ok': False, 'description': f'bad {TOKEN}'})
    assert NotificationService.send_telegram(TOKEN, CHAT, 'm') is False
    fake_post(monkeypatch, status=404, body={'ok': False, 'description': f'no_service {HOOK}'})
    assert NotificationService.send_slack(HOOK, 'm') is False
    out = capsys.readouterr().out
    assert 'HTTP 400' in out and 'HTTP 404' in out and 'no_service' in out
    assert TOKEN not in out and 'SECRETSECRET' not in out


def test_send_alert_reports_failed_channels(monkeypatch):
    monkeypatch.setenv('SLACK_WEBHOOK_URL', HOOK)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', TOKEN)
    monkeypatch.setenv('TELEGRAM_CHAT_ID', CHAT)
    fake_post(monkeypatch, status=500)
    results = NotificationService.send_alert(None, 'm')
    assert dict(results) == {'slack': False, 'telegram': False} and not results


def test_exception_message_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr(notifications.requests, 'post',
                        MagicMock(side_effect=RuntimeError(f'conn error for bot{TOKEN}/sendMessage')))
    assert NotificationService.send_telegram(TOKEN, CHAT, 'm') is False
    assert TOKEN not in capsys.readouterr().out


# ---------- Telegram feedback ----------

def callback(alert_id, verdict, chat=CHAT, photo=False):
    message = {'message_id': 9, 'chat': {'id': int(chat)}}
    message['caption' if photo else 'text'] = '🌧️ Rain is moving towards you'
    if photo:
        message['photo'] = [{}]
    return {'update_id': 100, 'callback_query': {
        'id': 'cb1', 'from': {'id': int(chat)}, 'data': f'fb:{alert_id}:{verdict}', 'message': message}}


def test_callback_from_foreign_chat_is_rejected(monkeypatch, alert):
    mock = fake_post(monkeypatch)
    assert NotificationService.handle_telegram_update(callback(alert.id, 1, chat='999'), TOKEN, CHAT) is False
    assert db.session.get(Alert, alert.id).user_feedback is None
    mock.assert_not_called()  # no answer, no edit for strangers


@pytest.mark.parametrize('verdict,expected', [(1, True), (0, False)])
def test_feedback_recorded_and_message_edited(monkeypatch, alert, verdict, expected):
    mock = fake_post(monkeypatch)
    assert NotificationService.handle_telegram_update(callback(alert.id, verdict), TOKEN, CHAT) is True
    saved = db.session.get(Alert, alert.id)
    assert saved.user_feedback is expected and saved.feedback_timestamp is not None
    methods = [c.args[0].rsplit('/', 1)[1] for c in mock.call_args_list]
    assert methods == ['answerCallbackQuery', 'editMessageText']
    assert 'Recorded' in mock.call_args.kwargs['json']['text']


def test_photo_message_edits_caption_and_unknown_alert(monkeypatch, alert):
    mock = fake_post(monkeypatch)
    assert NotificationService.handle_telegram_update(callback(alert.id, 1, photo=True), TOKEN, CHAT)
    assert mock.call_args.args[0].endswith('/editMessageCaption')
    assert NotificationService.handle_telegram_update(callback(9999, 1), TOKEN, CHAT) is False


def test_test_button_saves_nothing(monkeypatch, alert):
    fake_post(monkeypatch)
    assert NotificationService.handle_telegram_update(callback(0, 1), TOKEN, CHAT) is False
    assert db.session.get(Alert, alert.id).user_feedback is None


def test_poll_records_feedback_and_persists_offset(monkeypatch, alert):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', TOKEN)
    monkeypatch.setenv('TELEGRAM_CHAT_ID', CHAT)
    updates = {'ok': True, 'result': [callback(alert.id, 1)]}
    mock = fake_post(monkeypatch, body=updates)
    assert NotificationService.poll_telegram_updates(None) == 1
    assert 'offset' not in mock.call_args_list[0].kwargs['json']
    with open(notifications.OFFSET_FILE) as f:
        assert json.load(f) == {'bot': '123456', 'offset': 101}

    mock.reset_mock()
    fake_post(monkeypatch, body={'ok': True, 'result': []})
    NotificationService.poll_telegram_updates(None)
    assert notifications.requests.post.call_args.kwargs['json']['offset'] == 101


def test_poll_is_noop_without_telegram(monkeypatch, app_ctx):
    mock = fake_post(monkeypatch)
    assert NotificationService.poll_telegram_updates(None) == 0
    mock.assert_not_called()
