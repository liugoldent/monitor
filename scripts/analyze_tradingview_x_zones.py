#!/usr/bin/env python3
"""Classify TradingView X zones from point-in-time chart screenshots.

The detector reads the two actual visible Trendlines-with-Breaks extensions from
pixels on the live right side of each chart. It intentionally ignores legend
values and older completed X structures on the left side.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def current_bar_x(arr: np.ndarray) -> tuple[float, dict]:
    r, g, b = (arr[:, :, i].astype(int) for i in range(3))
    red = (r > 100) & (r > g + 30) & (r > b + 20)
    green = (g > 70) & (g > r + 25) & (g > b + 5)
    counts = (red | green)[80:780, 900:1310].sum(axis=0)
    xs = np.nonzero(counts >= 3)[0] + 900
    if not len(xs):
        return 1289.0, {"method": "fallback", "reason": "no candle pixels"}

    groups: list[list[int]] = [[int(xs[0])]]
    for x in xs[1:]:
        if x - groups[-1][-1] <= 2:
            groups[-1].append(int(x))
        else:
            groups.append([int(x)])
    eligible = [group for group in groups if len(group) >= 4 and group[-1] - group[0] >= 3]
    if not eligible:
        return 1289.0, {"method": "fallback", "reason": "no candle cluster"}

    group = eligible[-1]
    return (group[0] + group[-1]) / 2, {
        "method": "pixel-cluster",
        "range": [group[0], group[-1]],
        "width": group[-1] - group[0] + 1,
    }


def close_y(arr: np.ndarray) -> tuple[float, dict]:
    band = arr[80:760, 55:1300].astype(int)
    diffs = np.max(np.abs(band[:, 1:] - band[:, :-1]), axis=2)
    scores = (diffs > 5).sum(axis=1)
    index = int(scores.argmax())
    return float(index + 80), {"transition_score": int(scores[index])}


def line_fit(arr: np.ndarray, color: str) -> dict:
    height, width = arr.shape[:2]
    yy, xx = np.mgrid[:height, :width]
    r, g, b = (arr[:, :, i].astype(int) for i in range(3))

    # The live pair extends through the right side. Restricting this range keeps
    # older completed X structures from outvoting the current visible pair.
    crop = (xx >= 1050) & (xx <= 1400) & (yy >= 70) & (yy <= 780)
    if color == "red":
        mask = crop & (r > g + 5) & (r > b + 3) & (r > 35)
        slopes = np.linspace(-2.0, -0.02, 991)
    else:
        mask = crop & (g > r + 4) & (b > r + 3) & (g > 35)
        slopes = np.linspace(0.02, 2.0, 991)

    y, x = np.nonzero(mask)
    best: tuple[int, float, int] | None = None
    for slope in slopes:
        intercepts = np.rint(y - slope * x).astype(int) + 5000
        histogram = np.bincount(intercepts, minlength=10000)
        score = np.convolve(histogram, np.ones(5, dtype=int), mode="same")
        position = int(score.argmax())
        candidate = (int(score[position]), float(slope), position - 5000)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        raise ValueError(f"No {color} trendline pixels")

    score, slope, intercept = best
    residual = np.abs(y - (slope * x + intercept))
    inliers = residual <= 3.0
    xi = x[inliers]
    yi = y[inliers]
    if len(xi) >= 20:
        slope, intercept = np.polyfit(xi, yi, 1)
    # TradingView renders the projected part of a trendline with a much lower
    # opacity than the solid anchor segment. Keep the stricter mask for fitting
    # the line, but accept softly tinted projected pixels as right-side support.
    if color == "red":
        support_mask = crop & (r > g) & (r > b) & (r > 25)
    else:
        support_mask = crop & (g > r) & (b > r) & (g > 25)
    support_y, support_x = np.nonzero(support_mask)
    support_residual = np.abs(support_y - (slope * support_x + intercept))
    support_x = support_x[support_residual <= 3.0]

    return {
        "color": color,
        "slope": float(slope),
        "intercept": float(intercept),
        "score": score,
        "inliers": int(len(xi)),
        "span": int(xi.max() - xi.min()) if len(xi) else 0,
        "right_support": int((support_x >= 1280).sum()),
        "far_right_support": int((support_x >= 1330).sum()),
        "support_max_x": int(support_x.max()) if len(support_x) else -1,
        "mask_pixels": int(len(x)),
    }


def classify(path: Path, evidence_dir: Path | None) -> dict:
    image = Image.open(path).convert("RGB")
    arr = np.array(image)
    x_now, x_meta = current_bar_x(arr)
    y_close, close_meta = close_y(arr)
    red = line_fit(arr, "red")
    teal = line_fit(arr, "teal")
    red_y = red["slope"] * x_now + red["intercept"]
    teal_y = teal["slope"] * x_now + teal["intercept"]
    tolerance = 4.0

    above_red = y_close < red_y - tolerance
    above_teal = y_close < teal_y - tolerance
    before_cross = red_y >= teal_y - tolerance
    between = min(red_y, teal_y) + tolerance < y_close < max(red_y, teal_y) - tolerance
    if above_red and above_teal:
        zone = "1"
        relation = "收盤高於紅、綠兩條實際 X 線"
    elif before_cross and between:
        zone = "4"
        relation = "交叉前，收盤位於紅、綠兩條實際 X 線之間"
    else:
        zone = "other"
        if y_close > red_y + tolerance and y_close > teal_y + tolerance:
            relation = "收盤低於紅、綠兩條實際 X 線"
        elif between:
            relation = "收盤位於兩線之間，但兩線已交叉"
        else:
            relation = "收盤未落入 1 區或交叉前 4 區"

    quality_ok = (
        close_meta["transition_score"] >= 350
        and red["score"] >= 80
        and teal["score"] >= 80
        and red["span"] >= 80
        and teal["span"] >= 80
        # TradingView may stop the solid anchor segment at a recent pivot and
        # render only an extremely faint projected continuation. The fitted
        # line still comes from visible chart pixels; allow roughly four daily
        # bars between that segment and the current candle while rejecting old
        # X structures farther to the left.
        and red["support_max_x"] >= x_now - 60
        and teal["support_max_x"] >= x_now - 60
    )
    result = {
        "code": path.stem,
        "zone": zone,
        "relation": relation,
        "quality_ok": quality_ok,
        "current_x": x_now,
        "close_y": y_close,
        "red_y": float(red_y),
        "teal_y": float(teal_y),
        "before_cross": bool(before_cross),
        "x_meta": x_meta,
        "close_meta": close_meta,
        "red": red,
        "teal": teal,
    }

    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        diagnostic = image.crop((650, 60, 1410, 790)).copy()
        draw = ImageDraw.Draw(diagnostic)
        x_offset, y_offset = 650, 60
        for fit, line_color in ((red, (255, 80, 90)), (teal, (0, 220, 195))):
            x1, x2 = 700, 1400
            y1 = fit["slope"] * x1 + fit["intercept"]
            y2 = fit["slope"] * x2 + fit["intercept"]
            draw.line((x1 - x_offset, y1 - y_offset, x2 - x_offset, y2 - y_offset), fill=line_color, width=3)
        draw.line((x_now - x_offset, 0, x_now - x_offset, 730), fill=(255, 255, 0), width=2)
        draw.line((0, y_close - y_offset, 760, y_close - y_offset), fill=(255, 255, 255), width=2)
        draw.rectangle((0, 0, 360, 34), fill=(0, 0, 0))
        draw.text((5, 5), f"{path.stem} zone={zone} quality={quality_ok}", fill=(255, 255, 255))
        diagnostic.save(evidence_dir / path.name)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("screenshots", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = [classify(path, args.evidence_dir) for path in sorted(args.screenshots.glob("*.png"))]
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [result["code"] for result in results if not result["quality_ok"]]
    print(json.dumps({"count": len(results), "quality_failures": failures}, ensure_ascii=False))
    raise SystemExit(2 if failures else 0)


if __name__ == "__main__":
    main()
