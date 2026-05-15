"""Draft H-loss guard v2.

This is a research draft and is not wired into `webhook_server.py`.

Compared with `strategy_h_loss_guard.py`, v2 is designed to let successful
reverse-guard trades run until the H position changes, because the available
backtest showed early giveback exits cut the biggest compensation trades too
soon. It still keeps hard stops so the second account does not become an
unbounded hedge.
"""

from __future__ import annotations

import strategy_h_loss_guard as base


# Entry: trigger slightly earlier than the current production guard.
SOFT_LOSS_POINTS = -150.0
HARD_LOSS_POINTS = -420.0
INVALIDATION_SCORE = 3

# Risk: first entry is still capped. After add, cap the combined open loss.
SECOND_STOP_LOSS_POINTS = -220.0
SECOND_AFTER_ADD_TOTAL_STOP_POINTS = -220.0

# Let the hedge run until H changes instead of taking quick profit/trailing out.
SECOND_TAKE_PROFIT_POINTS = 99999.0
SECOND_TRAIL_ARM_POINTS = 99999.0
SECOND_TRAIL_FLOOR_POINTS = -99999.0

# Scale only after the reverse leg has already moved meaningfully in profit.
SECOND_ADD_PROFIT_POINTS = 220.0
SECOND_ADD_CONFIRM_SCORE = 2

# Disabled in v2; total stop above is the real after-add risk cap.
SECOND_AFTER_ADD_PROTECT_POINTS = -99999.0


def _second_side_confirm_score(side: str) -> int:
    return sum(
        1
        for tf in ("1", "3", "5")
        if base._is_second_side_supported(side, base._get_latest_row(base.WEBHOOK_CSV_BY_TF[tf]))
    )


def _build_guard_signal(position: dict) -> dict | None:
    side = position["side"]
    entry_price = position["price"]
    latest_1m = base._get_latest_row(base.WEBHOOK_CSV_BY_TF["1"])
    close = base._row_close(latest_1m)
    if close is None:
        return None

    unrealized = base._unrealized_points(side, entry_price, close)
    tf_rows = {tf: base._get_latest_row(path) for tf, path in base.WEBHOOK_CSV_BY_TF.items()}
    invalid_tfs = [tf for tf, row in tf_rows.items() if base._is_tf_invalid(side, row)]
    mxf = base._get_latest_mxf()
    mxf_invalid = base._is_mxf_invalid(side, mxf)

    reasons: list[str] = []
    if unrealized <= HARD_LOSS_POINTS:
        reasons.append(f"hard loss cap {HARD_LOSS_POINTS:.0f}pts")

    if unrealized <= SOFT_LOSS_POINTS and len(invalid_tfs) >= INVALIDATION_SCORE:
        reasons.append(f"{len(invalid_tfs)} timeframe invalidation: {','.join(invalid_tfs)}")

    if unrealized <= SOFT_LOSS_POINTS and mxf_invalid and len(invalid_tfs) >= 2:
        reasons.append("MXF confirms opposite pressure")

    if not reasons:
        return None

    return {
        "side": side,
        "entry_price": entry_price,
        "close": close,
        "unrealized_points": unrealized,
        "reason": "; ".join(reasons),
        "tf_score": len(invalid_tfs),
        "mxf_signal": mxf.get("signal", ""),
        "mxf_trend": mxf.get("trend", ""),
        "position_timestamp": position["timestamp"],
    }


def _send_second_entry_signal(h_position: dict, guard_signal: dict, state: dict) -> None:
    h_key = base._position_key(h_position)
    if base._get_second_position(state) is not None:
        return
    if state.get("last_second_entry_h_key") == h_key:
        return

    signal = base._build_second_entry_signal(h_position, guard_signal)
    base._append_guard_signal("entry", signal)
    state["last_second_entry_h_key"] = h_key
    state["second_position_side"] = signal["side"]
    state["second_position_entry_price"] = signal["entry_price"]
    state["second_add_entry_price"] = ""
    state["second_position_quantity"] = 1
    state["second_reference_h_key"] = h_key
    state["second_max_favorable_points"] = 0.0
    state["last_entry_signal_at"] = base.now_str()
    base._save_state(state)

    side_label = "多單" if signal["side"] == "bull" else "空單"
    h_side_label = "多單" if h_position["side"] == "bull" else "空單"
    message = (
        f"第二帳號 V2 反向進場訊號：{side_label}\n"
        f"第一帳號 H1 {h_side_label} 看錯擴大，第二帳號反向進場\n"
        f"進場價={signal['entry_price']}，原因：{signal['reason']}"
    )
    base.send_loss_guard_message(message)


def _send_second_add_signal(state: dict, second_position: dict, close: float, first_unrealized: float) -> None:
    side = second_position["side"]
    score = _second_side_confirm_score(side)
    if (
        second_position["quantity"] >= 2
        or first_unrealized < SECOND_ADD_PROFIT_POINTS
        or score < SECOND_ADD_CONFIRM_SCORE
    ):
        return

    signal = base._build_add_signal(
        side,
        close,
        second_position["entry_price"],
        close,
        (
            f"v2 scale in after +{SECOND_ADD_PROFIT_POINTS:.0f}pts, "
            f"{score}/3 short timeframes support"
        ),
        second_position["h_position_key"],
    )
    base._append_guard_signal("add", signal)
    state["second_add_entry_price"] = close
    state["second_position_quantity"] = 2
    state["last_add_signal_at"] = base.now_str()
    base._save_state(state)

    side_label = "多單" if side == "bull" else "空單"
    message = (
        f"第二帳號 V2 加碼訊號：{side_label} 加 1 口\n"
        f"第一口進場={second_position['entry_price']}，加碼價={close}，"
        f"第一口浮盈={first_unrealized:.1f} 點\n"
        f"確認={score}/3 個短週期支持"
    )
    base.send_loss_guard_message(message)


def _manage_second_position(h_position: dict | None, state: dict) -> bool:
    """Manage v2 second-account position with a combined after-add stop."""
    second_position = base._get_second_position(state)
    if second_position is None:
        return False

    close = base._get_latest_close()
    if close is None:
        return False

    side = second_position["side"]
    entry_price = second_position["entry_price"]
    first_unrealized = base._unrealized_points(side, entry_price, close)
    max_favorable = max(second_position["max_favorable_points"], first_unrealized)
    state["second_max_favorable_points"] = max_favorable

    _send_second_add_signal(state, second_position, close, first_unrealized)
    second_position = base._get_second_position(state) or second_position

    entries = [entry_price]
    if second_position.get("add_entry_price") is not None:
        entries.append(second_position["add_entry_price"])
    total_unrealized = sum(base._unrealized_points(side, entry, close) for entry in entries)

    reason = ""
    h_key = base._position_key(h_position) if h_position else ""
    if h_key and h_key != second_position["h_position_key"]:
        reason = "H1 position changed"
    elif second_position["quantity"] >= 2 and total_unrealized <= SECOND_AFTER_ADD_TOTAL_STOP_POINTS:
        reason = f"second-account after-add total stop {SECOND_AFTER_ADD_TOTAL_STOP_POINTS:.0f}pts"
    elif second_position["quantity"] < 2 and first_unrealized <= SECOND_STOP_LOSS_POINTS:
        reason = f"second-account stop loss {SECOND_STOP_LOSS_POINTS:.0f}pts"

    if not reason:
        base._save_state(state)
        return False

    signal = base._build_exit_signal(side, entry_price, close, reason, second_position["h_position_key"])
    signal["unrealized_points"] = total_unrealized
    base._append_guard_signal("exit", signal)
    base._clear_second_position(state)
    state["last_exit_signal_at"] = base.now_str()
    base._save_state(state)

    side_label = "多單" if side == "bull" else "空單"
    message = (
        f"第二帳號出場訊號：{side_label}\n"
        f"進場={entry_price}，現價={close}，"
        f"第一口浮動={first_unrealized:.1f} 點，總浮動={total_unrealized:.1f} 點\n"
        f"原因：{reason}"
    )
    base.send_loss_guard_message(message)
    return True


def apply_h_loss_guard_v2_strategy() -> None:
    """Emit second-account V2 reverse-entry and exit Discord signals."""
    with base.LOSS_GUARD_LOCK:
        h_position = base._get_latest_h_position()
        state = base._load_state()

        if _manage_second_position(h_position, state):
            return

        if not h_position:
            return

        if base._get_second_position(state) is not None:
            return

        guard_signal = _build_guard_signal(h_position)
        if guard_signal:
            _send_second_entry_signal(h_position, guard_signal, state)
