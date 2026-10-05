"""Read the local credential file without dependencies or logging its values."""
import os
from pathlib import Path


def load_env(path):
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and value:
            os.environ.setdefault(key, value)


def selected_account():
    selection = os.getenv('CAPITAL_ACCOUNT_SELECT', '').strip()
    if not selection:
        return None
    if selection not in ('1', '2'):
        raise ValueError('CAPITAL_ACCOUNT_SELECT 請填 1、2，或留空')
    account = os.getenv(f'CAPITAL_ACCOUNT_{selection}', '').strip()
    if not account:
        raise ValueError(f'請填 CAPITAL_ACCOUNT_{selection}，或將 CAPITAL_ACCOUNT_SELECT 留空')
    return account
