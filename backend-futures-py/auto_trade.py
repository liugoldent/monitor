import shioaji as sj # 載入永豐金Python API
import os
import json
import requests
import csv
import time as pytime
from pathlib import Path
from collections import deque
from datetime import datetime
from datetime import time
from zoneinfo import ZoneInfo

def load_env_file(path: str = ".env") -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            stripped = line.strip()

            # Skip comments/empty lines
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue

            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

base_dir = os.path.dirname(os.path.abspath(__file__))
ca_path = os.getenv("CA_PATH") or os.path.join(base_dir, "Sinopac.pfx")
WEBHOOK_URL = "https://discord.com/api/webhooks/1379030995348488212/4wjckp5NQhvB2v-YJ5RzUASN_H96RqOm2fzmuz9H26px6cLGcnNHfcBBLq7AKfychT5w"
TRADE_LOG_PATH = Path(__file__).resolve().parent / "tv_doc" / "h_trade.csv"
WEBHOOK_DATA_PATH = Path(__file__).resolve().parent / "tv_doc" / "webhook_data_1min.csv"
POSITION_SIZE_STATE_PATH = Path(__file__).resolve().parent / "tv_doc" / "h_position_size_state.json"
POINT_VALUE = 10
# 下列門檻都用「點數」設定；實際比較時會乘上 POINT_VALUE 轉成單口 pnl。
ADD_POSITION_DRAWDOWN_POINTS = 1000
MAX_POSITION_DRAWDOWN_POINTS = 2000
PROPORTIONAL_POSITION_SIZE_REFERENCE_PRICE = 22910
EXIT_ADD_POSITION_DRAWDOWN_POINTS = 0
MDD_RESET_TOLERANCE_POINTS = 5

# A 是常駐核心部位：MDD 到 1000/2000 點時，把核心口數提高到 2/3 口；
# 達標後維持該口數，直到 MDD 歸 0 或連贏 3 次才回 1 口。
BASE_ENTRY_QUANTITY = 1
ADD_POSITION_ENTRY_QUANTITY = 2
MAX_POSITION_ENTRY_QUANTITY = 3


def _ensure_trade_log() -> None:
    if TRADE_LOG_PATH.exists():
        return
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADE_LOG_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "action", "side", "price", "pnl", "quantity"])


def _append_trade(
    action: str,
    side: str,
    price: float,
    pnl: float | None = None,
    quantity: int | None = None,
) -> None:
    _ensure_trade_log()
    timestamp = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    with TRADE_LOG_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [timestamp, action, side, price, "" if pnl is None else pnl, "" if quantity is None else quantity]
        )


def _load_position_size_state() -> dict:
    if not POSITION_SIZE_STATE_PATH.exists():
        return {}
    try:
        with POSITION_SIZE_STATE_PATH.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_position_size_state(state: dict) -> None:
    POSITION_SIZE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    with POSITION_SIZE_STATE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _get_trade_log_start_row() -> int:
    state = _load_position_size_state()
    value = state.get("mdd_start_after_trade_row", state.get("trade_log_start_row", 0))
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _get_initial_drawdown_pnl() -> float:
    state = _load_position_size_state()
    value = state.get("starting_mdd_points", state.get("initial_drawdown_points", 0))
    try:
        return max(0.0, float(value)) * POINT_VALUE
    except (TypeError, ValueError):
        return 0.0


def _get_virtual_position() -> tuple[str, float] | None:
    state = _load_position_size_state()
    side = str(state.get("virtual_position_side", "")).strip().lower()
    if side not in {"bull", "bear"}:
        return None
    try:
        entry_price = float(state.get("virtual_position_entry_price"))
    except (TypeError, ValueError):
        return None
    return side, entry_price


def _set_virtual_position(side: str, entry_price: float | None) -> None:
    if entry_price is None:
        return
    state = _load_position_size_state()
    state["virtual_position_side"] = side
    state["virtual_position_entry_price"] = entry_price
    state["virtual_position_since"] = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    _save_position_size_state(state)


def _sync_virtual_position_for_signal(signal_side: str, close_price: float | None) -> None:
    virtual_position = _get_virtual_position()
    if not virtual_position or close_price is None:
        return

    virtual_side, virtual_entry_price = virtual_position
    if virtual_side == signal_side:
        return

    pnl = _get_exit_pnl(virtual_side, close_price, virtual_entry_price)
    _append_trade("exiting", virtual_side, close_price, pnl, quantity=BASE_ENTRY_QUANTITY)
    _sync_current_drawdown_state()


def _iter_trade_rows_after_start() -> list[list[str]]:
    if not TRADE_LOG_PATH.exists():
        return []
    with TRADE_LOG_PATH.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    start_row = _get_trade_log_start_row()
    return rows[1 + start_row:]


def _get_last_entry() -> tuple[str, float] | None:
    for row in reversed(_iter_trade_rows_after_start()):
        if len(row) < 4:
            continue
        action = row[1].strip().lower()
        side = row[2].strip().lower()
        if action == "enter" and side in {"bull", "bear"}:
            try:
                return side, float(row[3])
            except ValueError:
                return None
    return None


def _parse_pnl_value(raw_value: object) -> float | None:
    raw = str(raw_value).strip()
    if raw == "":
        return None
    # CSV 內可能會出現全形負號或千分位逗號，先正規化再轉數字
    raw = raw.replace(",", "")
    raw = raw.replace("－", "-").replace("−", "-").replace("﹣", "-")
    try:
        return float(raw)
    except ValueError:
        return None


def _get_all_exiting_pnls() -> list[float]:
    pnls: list[float] = []
    for row in _iter_trade_rows_after_start():
        if len(row) < 5:
            continue
        action = str(row[1]).strip().lower()
        if action != "exiting":
            continue
        pnl = _parse_pnl_value(row[4])
        if pnl is None:
            continue
        pnls.append(pnl)
    return pnls


def _get_consecutive_loss_count(pnls: list[float] | None = None) -> int:
    if pnls is None:
        pnls = _get_all_exiting_pnls()

    loss_count = 0
    for pnl in reversed(pnls):
        if pnl < 0:
            loss_count += 1
            continue
        break
    return loss_count


def _get_current_drawdown_pnl() -> float:
    # 只從 h_trade.csv 已寫入的 exiting 紀錄重算單口 MDD。
    # 進場前會先 closePosition() 或 _sync_virtual_position_for_signal() 寫入 exiting，
    # 然後 _get_entry_quantity() 才讀這些已落檔的 pnl 來決定下一筆口數。
    pnls = _get_all_exiting_pnls()

    equity = -_get_initial_drawdown_pnl()
    peak_equity = 0.0

    for pnl in pnls:
        equity += pnl
        peak_equity = max(peak_equity, equity)
    return peak_equity - equity


def _parse_trade_price(raw_value: object) -> float | None:
    raw = str(raw_value).replace(",", "").strip()
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _get_completed_h_trades_after_start() -> list[dict[str, float]]:
    completed_trades: list[dict[str, float]] = []
    current_entry_price: float | None = None

    for row in _iter_trade_rows_after_start():
        if len(row) < 5:
            continue

        action = str(row[1]).strip().lower()
        price = _parse_trade_price(row[3])

        if action == "enter":
            current_entry_price = price
            continue

        if action != "exiting":
            continue

        pnl = _parse_pnl_value(row[4])
        if pnl is None:
            current_entry_price = None
            continue

        # If the configured start row begins after the matching enter row, use exit price as
        # a conservative fallback so proportional sizing can still continue.
        entry_price = current_entry_price if current_entry_price is not None else price
        if entry_price is not None:
            completed_trades.append({"entry_price": entry_price, "pnl": pnl})
        current_entry_price = None

    return completed_trades


def _get_proportional_drawdown_threshold_points(entry_price: float | None) -> tuple[float, float]:
    reference_price = float(PROPORTIONAL_POSITION_SIZE_REFERENCE_PRICE)
    if entry_price is None or entry_price <= 0:
        entry_price = reference_price

    add_threshold_points = entry_price * ADD_POSITION_DRAWDOWN_POINTS / reference_price
    max_threshold_points = entry_price * MAX_POSITION_DRAWDOWN_POINTS / reference_price
    return add_threshold_points, max_threshold_points


def _apply_proportional_add_threshold(
    quantity: int,
    activated_level: int,
    current_drawdown_pnl: float,
    entry_price: float | None,
) -> tuple[int, int]:
    add_threshold_points, max_threshold_points = _get_proportional_drawdown_threshold_points(entry_price)
    current_drawdown_points = current_drawdown_pnl / POINT_VALUE

    if activated_level < MAX_POSITION_ENTRY_QUANTITY and current_drawdown_points >= max_threshold_points:
        return MAX_POSITION_ENTRY_QUANTITY, MAX_POSITION_ENTRY_QUANTITY
    if activated_level < ADD_POSITION_ENTRY_QUANTITY and current_drawdown_points >= add_threshold_points:
        return max(quantity, ADD_POSITION_ENTRY_QUANTITY), ADD_POSITION_ENTRY_QUANTITY
    return quantity, activated_level


def _get_proportional_a_core_state(
    completed_trades: list[dict[str, float]],
    next_entry_price: float | None = None,
) -> tuple[int, int, float, float]:
    # Future switch-over helper only: proportional thresholds keep 1000/2000 points
    # equivalent to the original 22910 price level, and reduce only one contract on reset.
    equity = -_get_initial_drawdown_pnl()
    peak_equity = 0.0
    current_drawdown_pnl = peak_equity - equity
    consecutive_win_count = 0
    a_core_qty = BASE_ENTRY_QUANTITY
    activated_level = BASE_ENTRY_QUANTITY

    for trade in completed_trades:
        a_core_qty, activated_level = _apply_proportional_add_threshold(
            a_core_qty,
            activated_level,
            current_drawdown_pnl,
            trade.get("entry_price"),
        )

        previous_drawdown_pnl = current_drawdown_pnl
        pnl = float(trade["pnl"])
        equity += pnl
        peak_equity = max(peak_equity, equity)
        current_drawdown_pnl = peak_equity - equity

        if pnl > 0:
            consecutive_win_count += 1
        else:
            consecutive_win_count = 0

        if (
            previous_drawdown_pnl > EXIT_ADD_POSITION_DRAWDOWN_POINTS * POINT_VALUE
            and current_drawdown_pnl <= MDD_RESET_TOLERANCE_POINTS * POINT_VALUE
        ):
            a_core_qty = max(BASE_ENTRY_QUANTITY, a_core_qty - 1)
            activated_level = BASE_ENTRY_QUANTITY
            consecutive_win_count = 0
        elif consecutive_win_count >= 3:
            a_core_qty = max(BASE_ENTRY_QUANTITY, a_core_qty - 1)
            consecutive_win_count = 0

    a_core_qty, activated_level = _apply_proportional_add_threshold(
        a_core_qty,
        activated_level,
        current_drawdown_pnl,
        next_entry_price,
    )
    add_threshold_points, max_threshold_points = _get_proportional_drawdown_threshold_points(next_entry_price)
    return a_core_qty, consecutive_win_count, add_threshold_points, max_threshold_points


def _update_proportional_position_size_detail_state(
    current_drawdown_pnl: float,
    consecutive_loss_count: int,
    consecutive_win_count: int,
    a_core_qty: int,
    add_threshold_points: float,
    max_threshold_points: float,
) -> None:
    state = _load_position_size_state()
    state["current_mdd_points"] = round(current_drawdown_pnl / POINT_VALUE, 2)
    state["current_mdd_pnl"] = round(current_drawdown_pnl, 2)
    state.pop("current_drawdown_points", None)
    state.pop("current_drawdown_pnl", None)
    state["consecutive_loss_count"] = consecutive_loss_count
    state["consecutive_win_count"] = consecutive_win_count
    state["position_size_mode"] = "proportional"
    state["position_size_reference_price"] = PROPORTIONAL_POSITION_SIZE_REFERENCE_PRICE
    state["proportional_add_threshold_points"] = round(add_threshold_points, 2)
    state["proportional_max_threshold_points"] = round(max_threshold_points, 2)
    state["b_overlay_active"] = False
    state["b_overlay_entry_rule"] = "disabled"
    state["b_overlay_exit_rule"] = "disabled"
    state["a_core_quantity"] = a_core_qty
    state["a_core_exit_rule"] = "A 達 2/3 口後維持；單口 MDD 歸 0 或連贏 3 次時減 1 口"
    state["b_overlay_quantity"] = 0
    state["target_entry_quantity"] = a_core_qty
    state["position_size_rule"] = (
        "A proportional: 以 22910 價位的 1000/2000 點換算為 4.36%/8.73%; "
        "下一筆依進場價換算門檻加到2/3口, MDD歸0或連贏3次時減1口; "
        "B overlay disabled; 總口數=A"
    )
    state["add_position_active"] = False
    state["current_drawdown_calculated_at"] = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    _save_position_size_state(state)


def _get_proportional_entry_quantity(next_entry_price: float | None = None) -> int:
    # Not wired into auto_trade() yet. Call this instead of _get_entry_quantity() after
    # the current MDD cycle is reset and you are ready to switch to proportional sizing.
    completed_trades = _get_completed_h_trades_after_start()
    pnls = [trade["pnl"] for trade in completed_trades]
    current_drawdown_pnl = _get_current_drawdown_pnl()
    consecutive_loss_count = _get_consecutive_loss_count(pnls)
    a_core_qty, consecutive_win_count, add_threshold_points, max_threshold_points = _get_proportional_a_core_state(
        completed_trades,
        next_entry_price,
    )
    _update_proportional_position_size_detail_state(
        current_drawdown_pnl,
        consecutive_loss_count,
        consecutive_win_count,
        a_core_qty,
        add_threshold_points,
        max_threshold_points,
    )
    return a_core_qty


def _sync_current_drawdown_state(
    current_drawdown_pnl: float | None = None,
    consecutive_loss_count: int | None = None,
) -> None:
    if current_drawdown_pnl is None:
        current_drawdown_pnl = _get_current_drawdown_pnl()
    if consecutive_loss_count is None:
        consecutive_loss_count = _get_consecutive_loss_count()

    state = _load_position_size_state()
    state["current_mdd_points"] = round(current_drawdown_pnl / POINT_VALUE, 2)
    state["current_mdd_pnl"] = round(current_drawdown_pnl, 2)
    state.pop("current_drawdown_points", None)
    state.pop("current_drawdown_pnl", None)
    state["consecutive_loss_count"] = consecutive_loss_count
    state["current_drawdown_calculated_at"] = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    _save_position_size_state(state)


def _get_a_core_state(pnls: list[float]) -> tuple[int, int, bool]:
    # A 核心口數用單口 MDD 加碼；達 1000/2000 點後維持 2/3 口，
    # 只在 MDD 歸零或連贏 3 次後直接回 1 口。
    equity = -_get_initial_drawdown_pnl()
    peak_equity = 0.0
    current_drawdown_pnl = peak_equity - equity
    consecutive_win_count = 0
    a_core_qty = BASE_ENTRY_QUANTITY
    reset_by_three_wins = False

    if current_drawdown_pnl >= MAX_POSITION_DRAWDOWN_POINTS * POINT_VALUE:
        a_core_qty = MAX_POSITION_ENTRY_QUANTITY
    elif current_drawdown_pnl >= ADD_POSITION_DRAWDOWN_POINTS * POINT_VALUE:
        a_core_qty = ADD_POSITION_ENTRY_QUANTITY

    for pnl in pnls:
        reset_by_three_wins = False
        previous_drawdown_pnl = current_drawdown_pnl
        equity += pnl
        peak_equity = max(peak_equity, equity)
        current_drawdown_pnl = peak_equity - equity

        if pnl > 0:
            consecutive_win_count += 1
        else:
            consecutive_win_count = 0

        if current_drawdown_pnl >= MAX_POSITION_DRAWDOWN_POINTS * POINT_VALUE:
            a_core_qty = MAX_POSITION_ENTRY_QUANTITY
        elif current_drawdown_pnl >= ADD_POSITION_DRAWDOWN_POINTS * POINT_VALUE:
            a_core_qty = max(a_core_qty, ADD_POSITION_ENTRY_QUANTITY)

        if (
            previous_drawdown_pnl > EXIT_ADD_POSITION_DRAWDOWN_POINTS * POINT_VALUE
            and current_drawdown_pnl <= MDD_RESET_TOLERANCE_POINTS * POINT_VALUE
        ):
            a_core_qty = BASE_ENTRY_QUANTITY
            consecutive_win_count = 0
        elif consecutive_win_count >= 3:
            a_core_qty = BASE_ENTRY_QUANTITY
            consecutive_win_count = 0
            reset_by_three_wins = True

    return a_core_qty, consecutive_win_count, reset_by_three_wins


def _update_position_size_detail_state(
    current_drawdown_pnl: float,
    consecutive_loss_count: int,
    consecutive_win_count: int,
    a_core_qty: int,
) -> None:
    state = _load_position_size_state()
    state["current_mdd_points"] = round(current_drawdown_pnl / POINT_VALUE, 2)
    state["current_mdd_pnl"] = round(current_drawdown_pnl, 2)
    state.pop("current_drawdown_points", None)
    state.pop("current_drawdown_pnl", None)
    state["consecutive_loss_count"] = consecutive_loss_count
    state["consecutive_win_count"] = consecutive_win_count
    state["b_overlay_active"] = False
    state["b_overlay_entry_rule"] = "disabled"
    state["b_overlay_exit_rule"] = "disabled"
    state["a_core_quantity"] = a_core_qty
    state["a_core_exit_rule"] = "A 達 2/3 口後維持；單口 MDD 歸 0 或連贏 3 次時回 1 口"
    state["b_overlay_quantity"] = 0
    state["target_entry_quantity"] = a_core_qty
    state["position_size_rule"] = (
        "A: MDD達1000點加到2口並維持, 達2000點加到3口並維持, MDD歸0或連贏3次回1口; "
        "B overlay disabled; 總口數=A"
    )
    # 舊欄位保留給既有人工檢查；目前 B overlay 停用。
    state["add_position_active"] = False
    state["current_drawdown_calculated_at"] = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    _save_position_size_state(state)


def _get_entry_quantity() -> int:
    # 讀取 h_trade.csv 內所有 exiting 的單口損益，用來計算連輸與單口 MDD。
    pnls = _get_all_exiting_pnls()
    # 目前單口回撤金額：只用已寫入 h_trade.csv 的 exiting pnl 重算，
    current_drawdown_pnl = _get_current_drawdown_pnl()
    # 從最近一筆 exiting 往前數，連續 pnl < 0 的筆數，只保留在 state/Discord 中供檢查。
    consecutive_loss_count = _get_consecutive_loss_count(pnls)
    a_core_qty, consecutive_win_count, _ = _get_a_core_state(pnls)
    _update_position_size_detail_state(
        current_drawdown_pnl,
        consecutive_loss_count,
        consecutive_win_count,
        a_core_qty,
    )
    return a_core_qty


def _get_position_size_message_detail() -> str:
    state = _load_position_size_state()

    a_core_qty = state.get("a_core_quantity", "?")
    drawdown_points = state.get("current_mdd_points", state.get("current_drawdown_points", "?"))
    consecutive_loss_count = state.get("consecutive_loss_count", "?")

    return (
        f"A {a_core_qty} 口（B overlay 停用，MDD {drawdown_points} 點，連輸 {consecutive_loss_count} 次）"
    )


def _get_latest_webhook_close() -> float | None:
    if not WEBHOOK_DATA_PATH.exists():
        return None
    with WEBHOOK_DATA_PATH.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        last_row = None
        for row in reader:
            last_row = row
    if not last_row:
        return None
    close_value = last_row.get("Close")
    if close_value is None:
        return None
    try:
        return float(str(close_value).replace(",", "").strip())
    except ValueError:
        return None


def _get_exit_pnl(side: str, exit_price: float | None, entry_price: float) -> float | None:
    if exit_price is None:
        return None
    if side == "bull":
        return (exit_price - entry_price) * 10
    if side == "bear":
        return (entry_price - exit_price) * 10
    return None


def _get_current_position_side(api) -> str | None:
    try:
        positions = api.list_positions(api.futopt_account)
    except Exception:
        return None

    if not positions:
        return None

    pos = positions[0]
    direction = pos['direction'] 
    
    if direction == "buy":
        return "bull"
    if direction == "sell":
        return "bear"
    return None


# 純下單func
def auto_trade(type):
    api = sj.Shioaji(simulation=False)
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))

    try:
        api_key = os.getenv("API_KEY")
        secret_key = os.getenv("SECRET_KEY")
        person_id = os.getenv("PERSON_ID")
        if not api_key or not secret_key:
            raise RuntimeError("Missing API_KEY or SECRET_KEY")
        if not person_id:
            raise RuntimeError("Missing PERSON_ID")

        api.login(api_key, secret_key)
        api.activate_ca(ca_path=ca_path, ca_passwd=person_id, person_id=person_id)
    except Exception as exc:
        message = f'[{testNow:%H:%M:%S}]：長線。Shioaji 登入/憑證啟用失敗，未送單：{exc}'
        print(message)
        send_discord_message(message)
        try:
            api.logout()
        except Exception:
            pass
        return

    try:
        if not os.path.exists(ca_path):
            print(f"❌ 找不到憑證檔案，目前嘗試路徑為: {ca_path}")
            return
        else:
            print(f"✅ 憑證檔案路徑: {ca_path}")

        contract = api.Contracts.Futures.TMF.TMFR1
        current_side = _get_current_position_side(api)

        if current_side == type:
            send_discord_message(f'[{testNow:%H:%M:%S}]：長線。忽略重複訊號，當前已是 {type}')
            api.logout()
            print(f'略過重複訊號: 已持有同方向倉位 {type}')
            return

        latest_close = _get_latest_webhook_close()
        # 先平實際部位，再用外部 H1 虛擬部位更新單口 MDD，最後才決定新倉口數。
        closed_actual_position = closePosition(api, latest_close)
        if not closed_actual_position:
            _sync_virtual_position_for_signal(type, latest_close)
        # 口數計算若因 state/json/csv 異常失敗，仍至少用 1 口進場，避免策略訊號完全漏單。
        entry_qty = BASE_ENTRY_QUANTITY
        try:
            entry_qty = _get_entry_quantity()
        except Exception as exc:
            message = f'[{testNow:%H:%M:%S}]：長線。口數計算失敗，改用預設 1 口進場：{exc}'
            print(message)
            send_discord_message(message)
        
        # 平倉後進新倉
        if type == 'bull':
            buyOne(api, contract, entry_qty)
            entry_price = latest_close
            _append_trade("enter", "bull", entry_price, quantity=entry_qty)
            _set_virtual_position("bull", entry_price)
            _sync_current_drawdown_state()
            position_size_detail = _get_position_size_message_detail()
            send_discord_message(
                f'[{testNow:%H:%M:%S}]：長線。近月多單進場 go bull，'
                f'總口數 {entry_qty}，{position_size_detail}'
            )

        if type == 'bear':
            sellOne(api, contract, entry_qty)
            entry_price = latest_close
            _append_trade("enter", "bear", entry_price, quantity=entry_qty)
            _set_virtual_position("bear", entry_price)
            _sync_current_drawdown_state()
            position_size_detail = _get_position_size_message_detail()
            send_discord_message(
                f'[{testNow:%H:%M:%S}]：長線。近月空單進場 go bear，'
                f'總口數 {entry_qty}，{position_size_detail}'
            )

        api.logout()
        print('送單完成')
    except Exception as e:
        api.logout()
        print('送單錯誤',e)


def closePosition(api, exit_price: float | None = None) -> bool:
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))
    try:
        positions = api.list_positions(api.futopt_account)
        print("目前倉位", positions)
        contract = api.Contracts.Futures.TMF.TMFR1
        last_entry = _get_last_entry()
        if exit_price is None:
            exit_price = _get_latest_webhook_close()

        if len(positions) > 0:
            pos = positions[0]
            print(pos['quantity'], '目前倉位數量') # 這個可以用
            pos_qty = int(pos['quantity'])
            if pos['direction'] == 'Buy':
                sellOne(api, contract, pos_qty)
                pnl = _get_exit_pnl("bull", exit_price, last_entry[1]) if last_entry else None
                _append_trade("exiting", "bull", exit_price, pnl, quantity=pos_qty)
                _sync_current_drawdown_state()
                pnl_text = "未知" if pnl is None else round(pnl, 2)
                send_discord_message(
                    f'[{testNow:%H:%M:%S}] 長線。多單平倉，丟空單平 {pos_qty} 口，'
                    f'單口 pnl {pnl_text}'
                )
                return True
            if pos['direction'] == 'Sell':
                buyOne(api, contract, pos_qty)
                pnl = _get_exit_pnl("bear", exit_price, last_entry[1]) if last_entry else None
                _append_trade("exiting", "bear", exit_price, pnl, quantity=pos_qty)
                _sync_current_drawdown_state()
                pnl_text = "未知" if pnl is None else round(pnl, 2)
                send_discord_message(
                    f'[{testNow:%H:%M:%S}] 長線。空單平倉，丟多單平 {pos_qty} 口，'
                    f'單口 pnl {pnl_text}'
                )
                return True
        else:
            print("目前沒有倉位，不補寫實際平倉紀錄")
            return False
    except Exception as e:
        # api.logout()
        print('送單錯誤',e)
    return False


def buyOne(api, contract, quantity=1):
    order = api.Order(
        action=sj.constant.Action.Buy,               # action (買賣別): Buy, Sell
        price=0,                                    # price (價格)
        quantity=quantity,                        # quantity (委託數量)
        price_type=sj.constant.FuturesPriceType.MKT,        # price_type (委託價格類別): LMT(限價), MKT(市價), MKP(範圍市價)
        order_type=sj.constant.OrderType.IOC,           # order_type (委託條件): IOC, ROD, FOK
        octype=sj.constant.FuturesOCType.Auto,           # octype (倉別 ): Auto(自動), New(新倉), Cover(平倉), DayTrade(當沖)
        account=api.futopt_account                 # account (下單帳號)
    )
    print("委託內容", order)
    # 執行委託
    trade = api.place_order(contract, order, timeout=0)
    print("委託回傳內容", trade)


def sellOne(api, contract, quantity=1):
    order = api.Order(
        action=sj.constant.Action.Sell,               # action (買賣別): Buy, Sell
        price=0,                        # price (價格)
        quantity=quantity,                        # quantity (委託數量)
        price_type=sj.constant.FuturesPriceType.MKT,        # price_type (委託價格類別): LMT(限價), MKT(市價), MKP(範圍市價)
        order_type=sj.constant.OrderType.IOC,           # order_type (委託條件): IOC, ROD, FOK
        octype=sj.constant.FuturesOCType.Auto,           # octype (倉別 ): Auto(自動), New(新倉), Cover(平倉), DayTrade(當沖)
        account=api.futopt_account                 # account (下單帳號)
    )
    print("委託內容", order)
    # 執行委託
    trade = api.place_order(contract, order, timeout=0)
    print("委託回傳內容", trade)


def send_discord_message(content: str):
    payload = {
        "username": "NotifierBot",
        "content": content,
    }
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ 發送 Discord 訊息失敗: {e}")
