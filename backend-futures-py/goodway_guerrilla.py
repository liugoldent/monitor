"""Shared Goodway history reader, without monitor startup or Mongo dependencies."""
from __future__ import annotations

import math

GOODWAY_HISTORY_URL = 'https://tvapi.futures-op.com/history'
GOODWAY_GUERRILLA_SYMBOLS = ('TX-1#goodway_chip960', 'TX-2#goodway_chip960')


def fetch_goodway_history(symbol, now):
    import requests
    response = requests.get(
        GOODWAY_HISTORY_URL,
        params=dict(symbol=symbol, resolution='1',
                    **{'from': int(now.timestamp()) - 15 * 60},
                    to=int(now.timestamp()), ws='true', metaData='true',
                    agent='daddy960', product='web_goodway'),
        timeout=(3, 8))
    response.raise_for_status()
    payload = response.json()
    bars = payload.get('bars') if isinstance(payload, dict) else None
    if not isinstance(bars, list):
        raise ValueError(f'{symbol}: history 回應沒有 bars')
    return {bar['time']: bar for bar in bars
            if isinstance(bar, dict) and type(bar.get('time')) in (int, float)
            and math.isfinite(bar['time']) and 0 <= bar['time'] <= now.timestamp() * 1000}
