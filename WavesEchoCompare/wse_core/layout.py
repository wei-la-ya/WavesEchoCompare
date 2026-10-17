"""在文本合并前按 COST 标识及双主词条定位声骸卡片。"""

import re
from dataclasses import dataclass

from .ocr import OcrLine, merge_card_rows
from .parser import FIXED_MAIN, _infer_cost, _detect_cost, _is_fixed_main, normalize_attr, _extract_prop_rows


@dataclass(frozen=True)
class CardRegion:
    box: tuple[int, int, int, int]
    lines: list[OcrLine]


def _prop_lines(lines: list[OcrLine]) -> list[tuple[OcrLine, str, str]]:
    props: list[tuple[OcrLine, str, str]] = []
    for line in lines:
        if normalize_attr(line.text) is None:
            continue
        # 只在同一视觉行中配对名称与最近的右侧数值，避免跨卡借值。
        nearby = sorted(
            (
                other
                for other in lines
                if other.x >= line.x + line.w
                and abs(other.y + other.h / 2 - line.y - line.h / 2) <= max(3, min(line.h, other.h) * 0.5)
            ),
            key=lambda other: other.x,
        )
        row = _extract_prop_rows([line])
        if not row and nearby:
            text = f"{line.text} {nearby[0].text}"
            row = _extract_prop_rows([OcrLine(text, line.score, line.x, line.y, line.h, line.w)])
        if row:
            props.append((line, *row[0]))
    return props


def find_card_regions(lines: list[OcrLine], width: int, height: int) -> list[CardRegion]:
    anchors = [
        line
        for line in lines
        if _detect_cost([line], None) is not None
        or re.match(r"^(?:COST|C0?ST)(?:[^A-Z]|$)", line.text.strip().upper()) is not None
    ]
    props = _prop_lines(lines)
    for fixed_line, attr, value in props:
        for cost in FIXED_MAIN:
            if not _is_fixed_main(attr, value, cost):
                continue
            previous = [
                (line, name, number)
                for line, name, number in props
                if 0 < fixed_line.y - line.y <= max(line.h, fixed_line.h) * 3
                and abs(line.x - fixed_line.x) <= max(line.h, fixed_line.h) * 2
            ]
            if not previous:
                continue
            first, name, number = max(previous, key=lambda item: item[0].y)
            if _infer_cost([(name, number), (attr, value)]) != cost:
                continue
            if not any(
                abs(anchor.x - first.x) <= max(anchor.h, first.h) * 2
                and abs(anchor.y - first.y) <= max(anchor.h, first.h) * 5
                for anchor in anchors
            ):
                anchors.append(first)
    bands: list[list[OcrLine]] = []
    for anchor in sorted(anchors, key=lambda item: item.y):
        if bands and anchor.y - bands[-1][0].y <= max(anchor.h, bands[-1][0].h) * 3:
            bands[-1].append(anchor)
        else:
            bands.append([anchor])
    regions: list[CardRegion] = []
    for anchor in [anchor for band in bands for anchor in sorted(band, key=lambda item: item.x)]:
        margin = max(3, anchor.h // 2)
        left = max(0, anchor.x - margin)
        top = max(0, anchor.y - margin)
        right = min(
            (
                other.x - max(3, other.h // 2)
                for other in anchors
                if other.x > anchor.x + anchor.h * 3 and abs(other.y - anchor.y) <= max(other.h, anchor.h) * 5
            ),
            default=width,
        )
        bottom = min(
            (
                other.y - max(3, other.h // 2)
                for other in anchors
                if other.y > anchor.y + anchor.h * 5 and abs(other.x - anchor.x) <= max(other.h, anchor.h) * 2
            ),
            default=min(height, anchor.y + anchor.h * 15),
        )
        headers = [
            line.y
            for line in lines
            if top + anchor.h * 3 < line.y < bottom
            and left <= line.x < right
            and len(line.text) <= 6
            and line.text.endswith("技能")
        ]
        if headers:
            bottom = min(headers)
        selected = [line for line in lines if left <= line.x < right and top <= line.y < bottom]
        regions.append(CardRegion((left, top, right, bottom), merge_card_rows(selected)))
    return regions
