"""COST 标识优先与主词条推断的离线回归。"""

import pytest
from wse_core import parser
from wse_core.ocr import OcrLine
from wse_core.parser import EchoParseError, parse_echo_lines


def _lines(*texts: str) -> list[OcrLine]:
    return [OcrLine(text=text, score=0.99, x=0, y=i * 30, h=20) for i, text in enumerate(texts)]


@pytest.mark.parametrize(
    ("cost", "main"),
    [
        (1, "攻击18%"),
        (1, "生命22.8%"),
        (1, "防御18%"),
        (3, "攻击30%"),
        (3, "生命30%"),
        (3, "防御38%"),
        (3, "共鸣效率32%"),
        (3, "冷凝伤害加成30%"),
        (3, "热熔伤害加成30%"),
        (3, "导电伤害加成30%"),
        (3, "气动伤害加成30%"),
        (3, "衍射伤害加成30%"),
        (3, "湮灭伤害加成30%"),
        (4, "攻击33%"),
        (4, "生命33%"),
        (4, "防御41.8%"),
        (4, "暴击22%"),
        (4, "暴击伤害44%"),
        (4, "治疗效果加成26.4%"),
    ],
)
@pytest.mark.parametrize("with_fixed", [False, True])
def test_infer_cost_from_main(cost: int, main: str, with_fixed: bool) -> None:
    fixed_attr, fixed_value = parser.FIXED_MAIN[cost]
    texts = [main]
    if with_fixed:
        texts.append(f"{fixed_attr}{fixed_value}")
    subs = ["暴击10.5%", "暴击伤害21%", "攻击60", "防御50", "生命580"]
    entry = parse_echo_lines(_lines(*texts, *subs), None)
    assert entry.cost == cost
    assert entry.main_value == parser.MAIN_VALUE[entry.main_name][cost]
    assert entry.main2_value == (fixed_value if with_fixed else None)
    assert len(entry.subs) == 5
    assert not entry.partial


@pytest.mark.parametrize("marker", ["COST?", "COST2", "COST13", "COST4.0", "C0ST?"])
def test_unreadable_cost_falls_back_to_main(marker: str) -> None:
    entry = parse_echo_lines(_lines(marker, "导电伤害加成30%", "攻击100", "暴击10.5%"), None)
    assert entry.cost == 3
    assert entry.subs == [("暴击", "10.5%")]


def test_low_confidence_cost_falls_back_to_main() -> None:
    lines = _lines("COST1", "暴击22%", "攻击150", "暴击伤害21%")
    lines[0].score = 0.79
    assert parse_echo_lines(lines, None).cost == 4


def _unexpected_inference(rows: list[tuple[str, str]]) -> int | None:
    raise AssertionError(f"已有明确 COST 时不应推断: {rows}")


@pytest.mark.parametrize("marker", ["COST: 4", "C0ST4", "C4"])
def test_clear_cost_skips_inference(monkeypatch: pytest.MonkeyPatch, marker: str) -> None:
    monkeypatch.setattr(parser, "_infer_cost", _unexpected_inference)
    entry = parse_echo_lines(_lines(marker, "暴击22%", "攻击150", "暴击伤害21%"), None)
    assert entry.cost == 4


def test_command_cost_overrides_image_and_skips_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser, "_infer_cost", _unexpected_inference)
    entry = parse_echo_lines(_lines("COST4", "生命22.8%", "生命2280", "暴击10.5%"), 1)
    assert entry.cost == 1


def test_clear_cost_is_not_overridden_by_main() -> None:
    with pytest.raises(EchoParseError, match="cost1 不存在主词条"):
        parse_echo_lines(_lines("COST1", "暴击22%", "攻击150", "暴击伤害21%"), None)


def test_conflicting_cost_markers_fall_back_to_main() -> None:
    entry = parse_echo_lines(_lines("COST1", "COST4", "共鸣效率32%", "攻击100", "暴击10.5%"), None)
    assert entry.cost == 3


@pytest.mark.parametrize("fixed", ["攻击100", "攻击150"])
def test_fixed_main_resolves_equal_distance(fixed: str) -> None:
    entry = parse_echo_lines(_lines("攻击31.5%", fixed, "暴击10.5%"), None)
    assert entry.cost == (3 if fixed == "攻击100" else 4)


@pytest.mark.parametrize("fixed", ["攻击103", "攻击147", "生命2270"])
def test_fixed_main_tolerates_ocr_numeric_noise(fixed: str) -> None:
    cost, main = {"攻击103": (3, "攻击30%"), "攻击147": (4, "攻击33%"), "生命2270": (1, "攻击18%")}[fixed]
    entry = parse_echo_lines(_lines(main, fixed, "暴击10.5%"), None)
    assert entry.cost == cost
    assert (entry.main2_name, entry.main2_value) == parser.FIXED_MAIN[cost]


@pytest.mark.parametrize(
    "texts",
    [
        ["攻击31.5%", "暴击10.5%"],
        ["攻击30%", "攻击150", "暴击10.5%"],
        ["暴击10.5%", "攻击150", "暴击伤害21%"],
        ["攻击31.5%", "暴击10.5%", "攻击100"],
    ],
)
def test_ambiguous_or_conflicting_main_requests_cost(texts: list[str]) -> None:
    with pytest.raises(EchoParseError, match="提升预估3"):
        parse_echo_lines(_lines(*texts), None)


def test_cost_only_header_cluster_does_not_hide_props() -> None:
    lines = _lines("COST4")
    props = _lines("暴击22%", "攻击150", "暴击伤害21%")
    for line in props:
        line.y += 200
    entry = parse_echo_lines(lines + props, None)
    assert entry.cost == 4
    assert entry.main_name == "暴击"


def test_cost_marker_selects_matching_card() -> None:
    lines = _lines("攻击18%", "生命2280", "暴击10.5%", "暴击伤害21%", "防御50")
    card = _lines("COST4", "暴击22%", "攻击150", "攻击60")
    for line in card:
        line.y += 400
    entry = parse_echo_lines(lines + card, None)
    assert entry.cost == 4
    assert entry.main_name == "暴击"
    assert entry.subs == [("攻击", "60")]


def test_inference_supports_split_name_and_value_columns() -> None:
    lines = [
        OcrLine("导电伤害加成", 0.99, 0, 0, 20, 100),
        OcrLine("30.0%", 0.99, 150, 0, 20, 60),
        *_lines("攻击100", "暴击10.5%"),
    ]
    lines[2].y = 30
    lines[3].y = 60
    entry = parse_echo_lines(lines, None)
    assert entry.cost == 3
    assert entry.main_name == "属性伤害加成"
    assert entry.subs == [("暴击", "10.5%")]


def test_invalid_command_cost_is_rejected() -> None:
    with pytest.raises(EchoParseError, match="仅支持替换"):
        parse_echo_lines(_lines("攻击18%", "暴击10.5%"), 2)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7.9% +共鸣解放伤害加成", "共鸣解放伤害加成"),
        ("+共鸣解放伤害加成 8.6%", "共鸣解放伤害加成"),
        ("9.4% 共鸣技能伤害加成", "共鸣技能伤害加成"),
        ("共 鸣 技 能 伤 害 加 成 9.4%", "共鸣技能伤害加成"),
        ("普攻伤害加成 7.9%", "普攻伤害加成"),
        ("普通攻击伤害加成 7.9%", "普攻伤害加成"),
        ("重击伤害加成 7.9%", "重击伤害加成"),
        ("冷凝伤害加成 30%", "属性伤害加成"),
        ("热熔伤害加成 30%", "属性伤害加成"),
        ("导电伤害加成 30%", "属性伤害加成"),
        ("气动伤害加成 30%", "属性伤害加成"),
        ("衍射伤害加成 30%", "属性伤害加成"),
        ("湮灭伤害加成 30%", "属性伤害加成"),
        ("属性伤害加成 30%", "属性伤害加成"),
        ("伤害加成 7.9%", None),
        ("未知伤害加成 7.9%", None),
    ],
)
def test_damage_bonus_names_keep_their_category(text: str, expected: str | None) -> None:
    assert parser.normalize_attr(text) == expected


@pytest.mark.parametrize("name", ["共鸣技能伤害加成", "共鸣解放伤害加成", "普攻伤害加成", "重击伤害加成"])
def test_damage_type_substat_cannot_infer_cost3(name: str) -> None:
    with pytest.raises(EchoParseError, match="明确判断声骸 COST"):
        parse_echo_lines(_lines(f"{name}30%", "攻击100", "暴击10.5%"), None)


def test_screenshot_substat_names_and_values_are_preserved() -> None:
    entry = parse_echo_lines(
        _lines(
            "暴击伤害 44.0%",
            "攻击 150",
            "共鸣效率 10.0%",
            "暴击 7.5%",
            "暴击伤害 13.8%",
            "攻击 40",
            "7.9% +共鸣解放伤害加成",
        ),
        None,
    )
    assert entry.cost == 4
    assert entry.subs == [
        ("共鸣效率", "10.0%"),
        ("暴击", "7.5%"),
        ("暴击伤害", "13.8%"),
        ("攻击", "40"),
        ("共鸣解放伤害加成", "7.9%"),
    ]
