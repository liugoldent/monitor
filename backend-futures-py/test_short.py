import argparse

from auto_trade_shortCycle import (
    _get_api_client,
    _get_contract,
    _shutdown_api_client,
    amend_trade_price,
    buyOne,
    buyOneLimit,
    cancel_all_open_trades,
    cancel_trade,
    closePosition,
    describe_trade,
    get_latest_open_trade,
    list_open_trades,
    sellOne,
    sellOneLimit,
)


DEFAULT_ACTION = "buyOne"
DEFAULT_QUANTITY = 1
DEFAULT_PRICE = None
DEFAULT_SIDE = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Short cycle order test runner")
    parser.add_argument(
        "action",
        nargs="?",
        default=DEFAULT_ACTION,
        choices=[
            "buyOne",
            "sellOne",
            "buyOneLimit",
            "sellOneLimit",
            "closePosition",
            "listOpenTrades",
            "amendLastTradePrice",
            "cancelLastTrade",
            "cancelAllTrades",
        ],
    )
    parser.add_argument("--quantity", type=int, default=DEFAULT_QUANTITY)
    parser.add_argument("--price", type=float, default=DEFAULT_PRICE)
    parser.add_argument("--side", choices=["buy", "sell"], default=DEFAULT_SIDE)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.action == "closePosition":
        closePosition()
        return

    api = _get_api_client()
    contract = _get_contract(api)

    try:
        if args.action == "buyOne":
            buyOne(api, contract, quantity=args.quantity)
        elif args.action == "sellOne":
            sellOne(api, contract, quantity=args.quantity)
        elif args.action == "buyOneLimit":
            if args.price is None:
                raise ValueError("buyOneLimit 需要 --price")
            buyOneLimit(api, contract, price=args.price, quantity=args.quantity)
        elif args.action == "sellOneLimit":
            if args.price is None:
                raise ValueError("sellOneLimit 需要 --price")
            sellOneLimit(api, contract, price=args.price, quantity=args.quantity)
        elif args.action == "listOpenTrades":
            trades = list_open_trades(api)
            print(f"open trades: {len(trades)}")
            for index, trade in enumerate(trades, start=1):
                print(f"[{index}] {describe_trade(trade)}")
        elif args.action == "amendLastTradePrice":
            if args.price is None:
                raise ValueError("amendLastTradePrice 需要 --price")
            trade = amend_trade_price(api, price=args.price, quantity=args.quantity, side=args.side)
            print("updated trade", describe_trade(trade))
        elif args.action == "cancelLastTrade":
            trade = cancel_trade(api, side=args.side)
            print("cancelled trade", describe_trade(trade))
        elif args.action == "cancelAllTrades":
            trades = cancel_all_open_trades(api)
            print(f"cancelled trades: {len(trades)}")
            for index, trade in enumerate(trades, start=1):
                print(f"[{index}] {describe_trade(trade)}")
        else:
            latest_trade = get_latest_open_trade(api, side=args.side)
            print("latest trade", describe_trade(latest_trade) if latest_trade else None)
    finally:
        _shutdown_api_client()


if __name__ == "__main__":
    main()
