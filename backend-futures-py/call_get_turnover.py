import sys


def main() -> int:
    try:
        from monitor_turnover import run_turnover_once
    except Exception as exc:
        print(f"❌ 無法匯入 monitor_turnover.run_turnover_once(): {exc}")
        return 1

    try:
        run_turnover_once()
    except Exception as exc:
        print(f"❌ run_turnover_once() 執行失敗: {exc}")
        return 2

    print("✅ run_turnover_once() 執行完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
