import sys


def main() -> int:
    try:
        from monitor_tv_data import get_tv_data_etf_common
        from monitor_tv_data import get_tv_data_index_tw_code
    except Exception as exc:
        print(f"❌ 無法匯入 monitor_tv_data.get_tv_data_etf_common(): {exc}")
        return 1

    try:
        get_tv_data_etf_common()
        get_tv_data_index_tw_code()
    except Exception as exc:
        print(f"❌ get_tv_data_etf_common() 執行失敗: {exc}")
        return 2

    print("✅ get_tv_data_etf_common() 執行完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
