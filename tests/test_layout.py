from wse_core.ocr import OcrLine, _merge_lines
from wse_core.layout import find_card_regions
from wse_core.parser import parse_echo_lines


def _card(x: int, y: int, cost: int, with_marker: bool) -> list[OcrLine]:
    texts = [f"COST{cost}"] if with_marker else []
    texts += ["攻击30%" if cost == 3 else "攻击18%", "攻击100" if cost == 3 else "生命2280", "暴击10.5%"]
    return [OcrLine(text, 0.99, x, y + i * 30, 20, 100) for i, text in enumerate(texts)]


def test_side_by_side_cards_are_split_before_row_merge() -> None:
    regions = find_card_regions(_card(20, 10, 3, True) + _card(240, 10, 1, True), 460, 220)
    entries = [parse_echo_lines(region.lines, None) for region in regions]
    assert [entry.cost for entry in entries] == [3, 1]
    assert [entry.main2_value for entry in entries] == ["100", "2280"]
    assert all(entry.subs == [("暴击", "10.5%")] for entry in entries)


def test_stacked_cards_without_cost_use_both_main_rows() -> None:
    regions = find_card_regions(_card(20, 10, 3, False) + _card(20, 260, 1, False), 240, 470)
    assert len(regions) == 2
    assert [parse_echo_lines(region.lines, None).cost for region in regions] == [3, 1]


def test_same_row_numeric_baseline_and_adjacent_stat_do_not_merge() -> None:
    lines = [
        OcrLine("攻击", 0.99, 10, 20, 20, 50),
        OcrLine("100", 0.99, 100, 17, 20, 40),
        OcrLine("暴击", 0.99, 10, 38, 20, 50),
        OcrLine("10.5%", 0.99, 100, 36, 20, 50),
    ]
    merged = _merge_lines([lines, lines], [1.0, 1.0])
    assert [line.text for line in merged] == ["攻击 100", "暴击 10.5%"]


def test_nearly_aligned_cards_keep_left_to_right_order() -> None:
    regions = find_card_regions(_card(20, 11, 3, True) + _card(240, 10, 1, True), 460, 220)
    assert [parse_echo_lines(region.lines, None).cost for region in regions] == [3, 1]


def test_unreadable_card_is_kept_for_error_reporting() -> None:
    lines = _card(20, 10, 3, True) + [OcrLine("COST?", 0.6, 240, 10, 20, 80)]
    regions = find_card_regions(lines, 460, 220)
    assert len(regions) == 2
    assert regions[1].lines[0].text == "COST?"


def test_inline_prop_does_not_take_attribute_from_next_card() -> None:
    lines = _card(20, 10, 3, False) + [
        OcrLine("暴击伤害44%", 0.99, 240, 10, 20, 100),
        OcrLine("攻击150", 0.99, 240, 40, 20, 100),
    ]
    regions = find_card_regions(lines, 460, 220)
    assert len(regions) == 2
    assert parse_echo_lines(regions[0].lines, None).cost == 3
