"""
Tiny stdlib .env loader plus helpers for env-based notification config.

Real environment variables win over .env; .env values win over DB settings
(see NotificationService.effective_config).
"""
import os

BASEDIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
ENV_PATH = os.path.join(BASEDIR, '.env')

KEYS = ('SLACK_WEBHOOK_URL', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'PUBLIC_BASE_URL')


def load_env(path: str = ENV_PATH) -> None:
    """Load KEY=VALUE lines from `path` into os.environ (existing vars are kept)."""
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip().removeprefix('export ').strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def env(key: str) -> str:
    """Stripped env value, '' when unset."""
    return (os.environ.get(key) or '').strip()


def set_env_key(key: str, value: str, path: str = ENV_PATH) -> None:
    """Create/update KEY=value in the .env file (chmod 600), keeping other lines."""
    lines = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    new_line = f'{key}={value}'
    for i, line in enumerate(lines):
        if line.strip().removeprefix('export ').split('=', 1)[0].strip() == key:
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.chmod(path, 0o600)
    os.environ[key] = value


def mask(value: str) -> str:
    """Masked form for display: only the last 4 chars, never the full secret."""
    return f'…{value[-4:]}' if value else ''
