import argparse
import json
from pathlib import Path

from signalops.assistant.service import answer_deterministically


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="執行策略小助手的本機回歸評估。")
    parser.add_argument("dataset", type=Path, help="JSONL 評估資料集路徑。")
    return parser


def evaluate(dataset: Path) -> int:
    failures = 0
    cases = [json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]
    for case in cases:
        answer = answer_deterministically(case["question"])
        missing = [word for word in case.get("must_contain", []) if word not in answer.text]
        forbidden = [word for word in case.get("must_not_contain", []) if word in answer.text]
        if missing or forbidden:
            failures += 1
            print(f"失敗：{case['id']}，缺少={missing or '無'}，不應出現={forbidden or '無'}")
        else:
            print(f"通過：{case['id']}")
    print(f"評估完成：{len(cases) - failures}/{len(cases)} 通過")
    return failures


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(1 if evaluate(args.dataset) else 0)


if __name__ == "__main__":
    main()
