"""OCR 文本行 → 声骸词条结构; cost 主词条白名单强校验。"""

import re
from math import isclose
from dataclasses import field, dataclass

from .ocr import OcrLine

# 主词条满值表, 与 XW role_info_change.phantom_main_value 一致 (仅取实际可出现的档位)
MAIN_VALUE: dict[str, dict[int, str]] = {
    "攻击": {1: "18%", 3: "30%", 4: "33%"},
    "生命": {1: "22.8%", 3: "30%", 4: "33%"},
    "防御": {1: "18%", 3: "38%", 4: "41.8%"},
    "暴击": {4: "22%"},
    "暴击伤害": {4: "44%"},
    "共鸣效率": {3: "32%"},
    "治疗效果加成": {4: "26.4%"},
    "属性伤害加成": {3: "30%"},
}

# 各 cost 允许的主词条白名单 (cost1/cost3 绝无暴击/暴击伤害)
MAIN_WHITELIST: dict[int, tuple[str, ...]] = {
    1: ("攻击", "生命", "防御"),
    3: ("攻击", "生命", "防御", "属性伤害加成", "共鸣效率"),
    4: ("攻击", "生命", "防御", "暴击", "暴击伤害", "治疗效果加成"),
}

# 固定主词条 (第二个主词条): 满 25 级数值恒定 — cost4/3 固定攻击, cost1 固定生命
FIXED_MAIN: dict[int, tuple[str, str]] = {
    4: ("攻击", "150"),
    3: ("攻击", "100"),
    1: ("生命", "2280"),
}

# OCR 属性名 → 标准属性名; 匹配方式: 去空格后「包含别名即归一」
ATTR_ALIASES: tuple[tuple[str, str], ...] = (
    ("暴击伤害", "暴击伤害"),
    ("爆伤", "暴击伤害"),
    ("暴伤", "暴击伤害"),
    ("暴击率", "暴击"),
    ("暴击", "暴击"),
    ("共鸣效率", "共鸣效率"),
    ("充能", "共鸣效率"),
    ("共效", "共鸣效率"),
    ("治疗效果加成", "治疗效果加成"),
    ("治疗", "治疗效果加成"),
    ("共鸣技能伤害", "共鸣技能伤害加成"),
    ("共鸣解放伤害", "共鸣解放伤害加成"),
    ("普通攻击伤害", "普攻伤害加成"),
    ("普攻伤害", "普攻伤害加成"),
    ("重击伤害", "重击伤害加成"),
    ("属性伤害加成", "属性伤害加成"),
    ("冷凝伤害加成", "属性伤害加成"),
    ("热熔伤害加成", "属性伤害加成"),
    ("导电伤害加成", "属性伤害加成"),
    ("气动伤害加成", "属性伤害加成"),
    ("衍射伤害加成", "属性伤害加成"),
    ("湮灭伤害加成", "属性伤害加成"),
    ("百分比攻击", "攻击"),
    ("百分比生命", "生命"),
    ("百分比防御", "防御"),
    ("攻击", "攻击"),
    ("生命", "生命"),
    ("防御", "防御"),
)

_VALUE_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(%)?")
_COST_RE = re.compile(r"(?:COST|C0?ST)[^0-9]{0,3}([134])(?![\d.])|^C([134])$")
_COST_MIN_SCORE = 0.8


@dataclass
class EchoEntry:
    """一张声骸卡解析结果: 双主词条 + 副词条 (名称与截图数值)。

    cost1/3/4 满级声骸均为双主词条: 主1 (随机词条) + 主2 (固定词条),
    副词条 5 条。partial: True 表示副词条少于 4 条 (模糊截图漏识别)。
    """

    cost: int
    main_name: str
    main_value: str
    subs: list[tuple[str, str]] = field(default_factory=list)
    main2_name: str | None = None
    main2_value: str | None = None
    partial: bool = False


class EchoParseError(Exception):
    """解析失败, msg 是给用户看的文案。"""


# OCR 常见错字 → 正确属性开头 (实拍图实测高频错字); 命中后走正常包含匹配
_TYPO_FIX: tuple[tuple[str, str], ...] = (
    ("量击", "暴击"),
    ("桑击", "暴击"),
    ("爆伤", "暴击伤害"),
    ("玫击", "攻击"),
    ("玫势", "攻击"),
    ("共鸡", "共鸣"),
    ("充能", "共鸣"),
    ("冫台疗", "治疗"),
)


def normalize_attr(raw: str) -> str | None:
    text = raw.replace(" ", "").replace("+", "")
    if not text:
        return None
    for typo, fix in _TYPO_FIX:
        if typo in text:
            text = text.replace(typo, fix)
    for alias, std in ATTR_ALIASES:
        if alias in text:
            return std
    return None


def _parse_value(raw: str) -> str | None:
    """从 OCR 行提取数值, 保留 % 单位 (XW subProps 格式: '10.5%' / '60')。"""
    m = _VALUE_RE.search(raw.replace(" ", ""))
    if not m:
        return None
    num, pct = m.group(1), m.group(2)
    return f"{num}%" if pct else num


def _cluster_cards(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """按词条文本块的 Y 坐标把行聚成卡 (声骸截图常见为单卡; 多卡按 Y 空隙分组)。"""
    if not lines:
        return []
    ordered = sorted(lines, key=lambda ln: ln.y)
    cards: list[list[OcrLine]] = [[ordered[0]]]
    for line in ordered[1:]:
        prev = cards[-1][-1]
        # 词条行距通常 ≤ 1.8 行高; 超过视为跨卡/跨区
        if line.y - prev.y > max(prev.h, line.h) * 4:
            cards.append([line])
        else:
            cards[-1].append(line)
    return cards


def _detect_cost(lines: list[OcrLine], cmd_cost: int | None) -> int | None:
    if cmd_cost is not None:
        return cmd_cost
    costs: set[int] = set()
    for line in lines:
        if line.score < _COST_MIN_SCORE:
            continue
        text = line.text.replace(" ", "").upper()
        m = _COST_RE.search(text)
        if m:
            costs.add(int(m.group(1) or m.group(2)))
    return next(iter(costs)) if len(costs) == 1 else None


def _pure_value_line(text: str) -> str | None:
    """整行是纯数值 (可能带%), 用于「名称/数值分列」布局的数值行匹配。"""
    t = text.replace(" ", "")
    if not t or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?%?", t):
        return None
    return t


# 描述文案动词: 带这些词的行是套装/技能描述, 不是词条行
_DESC_VERBS = ("提升", "增加", "造成", "获得", "降低", "减少", "解锁", "强化")


def _is_desc_text(text: str) -> bool:
    """套装/技能描述文案过滤: 词条行格式固定为「属性名+数值」, 不会含动词。"""
    return any(v in text for v in _DESC_VERBS)


def _extract_prop_rows(card: list[OcrLine]) -> list[tuple[str, str]]:
    """从卡内文本行提取 (属性名, 数值) 列表。

    支持两种布局:
    A. 单行自含: '暴击 10.5%'
    B. 名称/数值分列: 名称行 '导电伤害加成' + 右侧紧邻的纯数值行 '30.0%'
    套装/技能描述文案 (含动词) 直接排除。
    """
    rows: list[tuple[str, str]] = []
    used: set[int] = set()
    ordered = sorted(card, key=lambda ln: (ln.y, ln.x))
    for i, line in enumerate(ordered):
        if i in used:
            continue
        text = line.text.strip()
        if not text or _is_desc_text(text):
            continue
        attr = normalize_attr(text)
        if attr is None:
            continue
        inline = _parse_value(text)
        if inline is not None:
            rows.append((attr, inline))
            used.add(i)
            continue
        # 无内联数值: 找紧随其下的纯数值行 (x 在名称行右侧, 垂直间距 ≤ 2 行高)
        matched_idx: int | None = None
        for j in range(i + 1, len(ordered)):
            cand = ordered[j]
            if cand.y - line.y > max(line.h, cand.h) * 2.5:
                break
            if j in used:
                continue
            val = _pure_value_line(cand.text)
            if val is None:
                continue
            if cand.x >= line.x + line.w // 2:
                matched_idx = j
                break
        if matched_idx is not None:
            rows.append((attr, ordered[matched_idx].text.strip()))
            used.add(i)
            used.add(matched_idx)
    return rows


def _main_from_rows(rows: list[tuple[str, str]]) -> tuple[str, str] | None:
    """主词条 = 第一个词条行。"""
    if not rows:
        return None
    return rows[0]


def _card_has_cost(card: list[OcrLine], cost: int) -> bool:
    """卡内是否出现与 cost 对应的 COST 字样 (多卡截图时用于选卡)。"""
    return _detect_cost(card, None) == cost


def _is_fixed_main(attr: str, value: str, cost: int) -> bool:
    fixed_attr, fixed_value = FIXED_MAIN[cost]
    if attr != fixed_attr or _pure_value_line(value) is None or "%" in value:
        return False
    return abs(float(value) - float(fixed_value)) <= float(fixed_value) * 0.15


def _split_main_rows(rows: list[tuple[str, str]], cost: int) -> tuple[tuple[str, str] | None, list[tuple[str, str]]]:
    # 固定主词条只在前两行查找，避免用副词条推断 COST。
    for i, (attr, value) in enumerate(rows[:2]):
        if _is_fixed_main(attr, value, cost):
            return FIXED_MAIN[cost], rows[:i] + rows[i + 1 :]
    return None, rows


def _infer_cost(rows: list[tuple[str, str]]) -> int | None:
    """按主词条满值选最近档位，固定主词条用于消歧与交叉校验。"""
    matches: list[tuple[int, float]] = []
    fixed_costs = {cost for cost in FIXED_MAIN if any(_is_fixed_main(attr, value, cost) for attr, value in rows[:2])}
    for cost in MAIN_WHITELIST:
        _, remaining = _split_main_rows(rows, cost)
        if not remaining:
            continue
        attr, value = remaining[0]
        if attr not in MAIN_WHITELIST[cost] or _pure_value_line(value) is None:
            continue
        full_value = float(MAIN_VALUE[attr][cost].rstrip("%"))
        distance = abs(float(value.rstrip("%")) - full_value)
        if distance <= full_value * 0.1:
            matches.append((cost, distance))
    if not matches:
        return None
    nearest = min(distance for _, distance in matches)
    costs = {
        cost
        for cost, distance in matches
        if isclose(distance, nearest, abs_tol=1e-9) and (not fixed_costs or cost in fixed_costs)
    }
    return next(iter(costs)) if len(costs) == 1 else None


def parse_echo_lines(lines: list[OcrLine], cmd_cost: int | None) -> EchoEntry:
    cost = _detect_cost(lines, cmd_cost)
    if cost is not None and cost not in MAIN_WHITELIST:
        raise EchoParseError("[鸣潮] 仅支持替换 cost1 / cost3 / cost4 声骸")

    cards = _cluster_cards(lines)
    # 多卡截图: 优先选带 COST 字样的卡; 无则全部参选 (声骸浮窗截图无 COST 字样)
    with_cost = [c for c in cards if cost is not None and _card_has_cost(c, cost)]
    candidates = [c for c in with_cost if _extract_prop_rows(c)] or cards
    best: list[tuple[str, str]] | None = None
    for card in candidates:
        rows = _extract_prop_rows(card)
        if best is None or len(rows) > len(best):
            best = rows

    if not best:
        raise EchoParseError("[鸣潮] 未能从截图中识别出主词条, 请发送更清晰的声骸详情截图")

    if cost is None:
        cost = _infer_cost(best)
    if cost is None:
        raise EchoParseError(
            "[鸣潮] 未能从 COST 标识或主词条明确判断声骸 COST, "
            "请发送更清晰的满级声骸详情截图, 或在命令中指定, 如: ww角色名提升预估3"
        )

    main2, remaining = _split_main_rows(best, cost)

    if not remaining:
        raise EchoParseError("[鸣潮] 未能从截图中识别出主词条, 请发送更清晰的声骸详情截图")

    # 主词条 1 数值必须等于该 cost 满值 (主词条数值恒定)
    # 防止强化确认弹窗等无主词条截图被副词条数值硬凑通过
    main_name = remaining[0][0]
    if main_name not in MAIN_WHITELIST[cost]:
        allow = "、".join(MAIN_WHITELIST[cost])
        raise EchoParseError(f"[鸣潮] cost{cost} 不存在主词条「{main_name}」(可选: {allow}), 识别可能有误, 请核对截图")
    main_full = float(MAIN_VALUE[main_name][cost].rstrip("%"))
    first_value = remaining[0][1].rstrip("%")
    try:
        if abs(float(first_value) - main_full) > main_full * 0.1:
            raise EchoParseError(
                "[鸣潮] 截图中未识别到主词条 (首个词条数值与满值不符), 请发送声骸详情截图, 不要使用强化/重构等页面"
            )
    except ValueError:
        raise EchoParseError("[鸣潮] 主词条数值识别异常, 请发送更清晰的声骸详情截图") from None

    entry = EchoEntry(cost=cost, main_name=main_name, main_value=MAIN_VALUE[main_name][cost])
    if main2 is not None:
        entry.main2_name, entry.main2_value = main2
    entry.subs = remaining[1:]
    if len(entry.subs) < 1:
        raise EchoParseError("[鸣潮] 未识别到副词条, 请发送声骸详情截图 (含词条列表)")
    # 满 25 级声骸应识别到 5 条副词条; 不足即提醒
    entry.partial = len(entry.subs) < 5
    return entry
