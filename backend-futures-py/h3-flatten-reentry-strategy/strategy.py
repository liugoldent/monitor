"""Hulk 3 notifications determine direction; H_UNIT determines order size."""
import re

POSITION = re.compile(r"(多|空)\s*(\d+)\s*口")


def parse_signal(text):
    if "浩克3" not in text or "訊號通知" not in text:
        return None
    matches = list(POSITION.finditer(text))
    if len(matches) != 1 or int(matches[0][2]) < 1:
        return None
    return 1 if matches[0][1] == "多" else -1
