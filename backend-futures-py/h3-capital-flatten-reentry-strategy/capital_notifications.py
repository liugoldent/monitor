"""Capital H3 Discord delivery, independent of broker credentials and execution."""
import json
from pathlib import Path
import urllib.request
from urllib.parse import urlparse

from h_core import TradingError

ENV_KEY = 'DISCORD_CAPITAL_H3_MDD_ADD_URL'


def read_webhook(path):
    value = ''
    try:
        lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        raise TradingError('無法讀取 backend-futures-py/.env 中的群益 Discord 設定') from None
    for line in lines:
        key, sep, candidate = line.strip().partition('=')
        if sep and key.strip() == ENV_KEY:
            value = candidate.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
    parsed = urlparse(value)
    if (parsed.scheme != 'https' or parsed.hostname not in ('discord.com', 'discordapp.com')
            or not parsed.path.startswith('/api/webhooks/') or parsed.username or parsed.password):
        raise TradingError('請在 backend-futures-py/.env 設定有效的 ' + ENV_KEY)
    return value


class Discord:
    def __init__(self, url, store):
        self.url, self.store = url, store

    def __call__(self, message):
        content = message.replace('【H3', '【群益 H3', 1)[:1900]
        try:
            request = urllib.request.Request(self.url, data=json.dumps(
                {'content': content, 'allowed_mentions': {'parse': []}}).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'User-Agent': 'Capital-H3/1.0'}, method='POST')
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read(1024)
                status = response.status
            self.store.audit('capital_discord_delivered', {'status': status, 'title': content.splitlines()[0]})
            return True
        except Exception as exc:
            # Record only the error type; HTTP exceptions may contain the webhook token.
            self.store.audit('capital_discord_failed', {'error': type(exc).__name__})
            return False


def stop_message(exc, *, pending, redact=lambda value: value):
    reason = redact(str(exc)) if type(exc) is TradingError else 'SDK或系統例外；詳細內容請在本機終端核對'
    return ('【群益 H3 修復進場｜異常停止】\n'
            f'原因：{reason}\n本機未確認委託：{bool(pending)}（不代表券商即時狀態）\n'
            '請核對庫存、委託及本機紀錄；不重送委託。')
