"""Operator-run diagnostics, legacy rejection review and a one-time manual test."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from h_core import TradingError, execute_h


def review_pending(broker, store, evidence_path, confirm, output=print):
    pending = store.pending()
    if pending is None:
        output('沒有未確認委託，不需解除。')
        return False
    if (pending.get('phase') != 'entry' or pending.get('previous') != 0
            or pending.get('quantity') != 1 or abs(pending.get('target', 0)) != 1):
        raise TradingError('此工具只核對空手買賣1口的新單拒絕；其他委託請逐筆人工核對')
    if not broker.replay_complete.is_set():
        raise TradingError('委託回補尚未完成，不解除委託阻擋')
    broker.check_outstanding()
    if broker.position() != 0:
        raise TradingError('目前不是空手，不解除舊委託阻擋')
    prepared = None
    for at, detail in store.db.execute("SELECT at,detail FROM audit WHERE event='prepared' ORDER BY id DESC"):
        if json.loads(detail) == pending:
            prepared = datetime.fromisoformat(at).astimezone(timezone(timedelta(hours=8)))
            break
    if prepared is None:
        raise TradingError('找不到對應的送單準備紀錄，不解除阻擋')
    raw = Path(evidence_path).read_bytes()
    evidence = json.loads(raw.decode('utf-8-sig'))
    if (evidence.get('mode') != 'read_only' or evidence.get('orders_sent') != 0
            or evidence.get('report_account_exact_match') is not True
            or evidence.get('account_suffix') != broker.account[-4:]
            or evidence.get('replay_complete') is not True):
        raise TradingError('拒單證據的帳號或回補狀態不符，不解除阻擋')
    matches = []
    c = broker.contract
    aliases = {c.quote_code, c.inventory_code, c.order_code, 'TMF' + c.settle_ym}
    for row in evidence.get('reports', []):
        if row.get('Type') == 'D':
            raise TradingError('證據包含成交回報，不能按零成交拒單解除阻擋')
        if not (row.get('MarketType') == 'TF' and row.get('Type') == 'N'
                and row.get('OrderErr') == 'Y' and row.get('OrderNo') == '00000'
                and row.get('Qty') == '1' and row.get('YearMonth1') == c.settle_ym
                and row.get('ComId') in aliases and row.get('ComId1') == c.order_code
                and row.get('BuySell', '').startswith('B' if pending['target'] > 0 else 'S')
                and row.get('SeqNo') and row.get('ErrorMsg')):
            continue
        try:
            at = datetime.strptime(row['Date'] + ' ' + row['Time'], '%Y%m%d %H:%M:%S').replace(tzinfo=prepared.tzinfo)
        except (KeyError, ValueError):
            continue
        if -1 <= (at - prepared).total_seconds() <= 10:
            matches.append(row)
    if len(matches) != 1:
        raise TradingError('拒單證據無法唯一對應舊委託的時間、商品及數量，不解除阻擋')
    row = matches[0]
    expected = f"RESOLVE REJECTED {broker.account[-4:]} {row['SeqNo']}"
    output(f"舊單時間 {row['Date']} {row['Time']}；帳號末碼 {broker.account[-4:]}；"
           f"{c.quote_code} 1口；券商新單拒絕、委託號00000；目前庫存0口。")
    output('歷史拒單原因：' + broker.redact(row['ErrorMsg']))
    answer = confirm('請先在券商端核對這筆拒單及零成交。核對完成後，由本人輸入 ' + expected + '：')
    if answer.strip() != expected:
        raise TradingError('未確認舊拒單核對，保留委託阻擋')
    # Recheck after the operator's review; never retry or replace the old order.
    broker.check_outstanding()
    if broker.position() != 0:
        raise TradingError('核對期間庫存已變動，保留委託阻擋')
    store.resolve_rejected(pending, dict(source='operator_reviewed_historical_rejection',
        operator_confirmed=True, account_suffix=broker.account[-4:],
        sequence=row['SeqNo'], report_date=row['Date'], report_time=row['Time'],
        evidence_sha256=hashlib.sha256(raw).hexdigest(), current_position=0,
        reason=broker.redact(row['ErrorMsg'])))
    output('舊拒單已核對並保留稽核紀錄；未重送委託。一般啟動可繼續等待新H3訊號。')
    return True


def manual_buy_one(broker, store, confirm, output=print):
    if store.pending():
        raise TradingError('有未確認委託，請先核對，不送正式測試單')
    if broker.position() != 0:
        raise TradingError('單次正式測試須空手；不平掉或加碼既有部位')
    store.db.execute("CREATE TABLE IF NOT EXISTS capital_manual_buy_one (id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL)")
    store.db.commit()
    if store.db.execute('SELECT 1 FROM capital_manual_buy_one').fetchone():
        raise TradingError('單次測試已有嘗試紀錄，不自動重送；請核對券商回報')
    expected = f'BUY 1 {broker.contract.quote_code} MKT IOC {broker.account[-4:]}'
    answer = confirm(f'【正式委託】帳號末碼 {broker.account[-4:]}，買進1口微型台指'
                     f'{broker.contract.quote_code}，MKT／IOC、新倉、非當沖。'
                     f'本人確認請輸入 {expected}：')
    if answer.strip() != expected:
        raise TradingError('使用者取消正式測試，未送單')
    with store.db:
        store.db.execute("INSERT INTO capital_manual_buy_one VALUES (1,'attempted')")
    result = execute_h(broker, 1, store, 'capital-manual-buy-one-v1')
    with store.db:
        store.db.execute("UPDATE capital_manual_buy_one SET status='confirmed' WHERE id=1")
    output(f'正式買進{result}口已確認成交與庫存；本次測試結束，不會自動平倉。')
    return result


def test_buy_one_with_review(broker, store, evidence_path, confirm, output=print):
    """One foreground workflow; each operator decision still has its own prompt."""
    if store.pending():
        evidence_path = Path(evidence_path)
        if not evidence_path.is_file():
            raise TradingError('有舊委託待核對，但找不到拒單證據；請以 --rejection-evidence 指定證據，不送測試單')
        output('先核對舊拒單；完成後才會進入新的一口正式買單確認。')
        review_pending(broker, store, evidence_path, confirm, output)
    return manual_buy_one(broker, store, confirm, output)
