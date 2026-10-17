"""替换面板声骸词条; cost3/cost1 双槽位取伤害更优者。"""

import copy
import asyncio
from typing import Callable, Sequence
from functools import partial
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.api.model import (
    Props,
    RoleDetailData,
)

from .parser import FIXED_MAIN, MAIN_VALUE, EchoEntry


class ReplaceError(Exception):
    """替换失败, msg 是给用户看的文案。"""


def _cost_slots(role_detail: RoleDetailData, cost: int) -> list[int]:
    """面板 equipPhantomList 中该 cost 的槽位下标列表。"""
    pd = role_detail.phantomData
    eq = pd.equipPhantomList if pd else None
    if not eq:
        return []
    return [i for i, ep in enumerate(eq) if ep and ep.cost == cost]


def _apply_entry(role_detail: RoleDetailData, entry: EchoEntry, slot_index: int) -> None:
    """把截图词条写进指定槽位: 双主词条 + 副词条按截图数值。

    cost1/3/4 满级声骸均为双主词条: mainProps[0]=随机主词条 (满值),
    mainProps[1]=固定主词条 (攻击150/100 或 生命2280)。XW rawData 实证此结构。
    """
    pd = role_detail.phantomData
    eq = pd.equipPhantomList if pd else None
    if not eq:
        raise ReplaceError("[鸣潮] 面板无声骸数据, 请先刷新面板")
    slot = eq[slot_index]
    if slot is None:
        raise ReplaceError("[鸣潮] 目标槽位无声骸, 请先刷新面板")
    main_name = entry.main_name
    if main_name == "属性伤害加成":
        if not role_detail.role.attributeName:
            raise ReplaceError("[鸣潮] 角色面板缺少属性信息，请先刷新面板")
        main_name = f"{role_detail.role.attributeName}伤害加成"
    main_value = MAIN_VALUE[entry.main_name][entry.cost]
    props = [Props(attributeName=main_name, attributeValue=main_value)]
    fixed_name, fixed_value = FIXED_MAIN[entry.cost]
    props.append(Props(attributeName=fixed_name, attributeValue=fixed_value))
    slot.mainProps = props
    slot.subProps = [Props(attributeName=name, attributeValue=value) for name, value in entry.subs]


@dataclass
class ReplaceResult:
    """择优结果: 替换后面板 + 选定槽位 + 该面板伤害求和 (择优时已算)。"""

    role_detail: RoleDetailData
    slot_indices: tuple[int, ...]
    damage_sum: float
    before: float
    candidate_count: int


def _rebuild_add_prop_list(role_detail: RoleDetailData) -> None:
    """替换声骸后重算 equipPhantomAddPropList (声骸词条汇总)。

    API 返回的该字段是静态汇总, 替换 phantomData 不会同步; 若总属性计算读取
    此字段而不读 phantomData, 就会出现「词条换了但总属性不变」。
    汇总规则: 遍历全部槽位主+副词条, 同名同单位 (百分比/固定) 数值相加。
    """
    pd = role_detail.phantomData
    eq = pd.equipPhantomList if pd else None
    if not eq:
        return
    merged: dict[tuple[str, bool], float] = {}
    order: list[tuple[str, bool]] = []
    for slot in eq:
        if slot is None:
            continue
        for prop in [*(slot.mainProps or []), *(slot.subProps or [])]:
            raw = prop.attributeValue.strip()
            is_pct = raw.endswith("%")
            try:
                num = float(raw.rstrip("%"))
            except ValueError:
                continue
            key = (prop.attributeName, is_pct)
            if key not in merged:
                order.append(key)
                merged[key] = 0.0
            merged[key] += num
    props: list[Props] = []
    for name, is_pct in order:
        val = merged[(name, is_pct)]
        text = f"{round(val, 1)}%" if is_pct else f"{int(round(val))}"
        props.append(Props(attributeName=name, attributeValue=text))
    role_detail.equipPhantomAddPropList = props


_COMPARE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="echo-compare")


def _replacement_plans(role_detail: RoleDetailData, entries: Sequence[EchoEntry]) -> list[tuple[int, ...]]:
    if not entries or len(entries) > 5:
        raise ReplaceError("[鸣潮] 每次请提供 1 至 5 个声骸")
    slots_by_cost = {entry.cost: _cost_slots(role_detail, entry.cost) for entry in entries}
    for cost, slots in slots_by_cost.items():
        count = sum(entry.cost == cost for entry in entries)
        if count > len(slots):
            raise ReplaceError(
                f"[鸣潮] 提供了 {count} 个 cost{cost} 声骸，面板仅有 {len(slots)} 个对应槽位，请减少截图"
            )
    plans: list[tuple[int, ...]] = [()]
    for entry in entries:
        plans = [plan + (slot,) for plan in plans for slot in slots_by_cost[entry.cost] if slot not in plan]
    return plans


def _build_candidate(
    role_detail: RoleDetailData, entries: Sequence[EchoEntry], slots: tuple[int, ...]
) -> RoleDetailData:
    candidate = copy.deepcopy(role_detail)
    for entry, slot in zip(entries, slots, strict=True):
        _apply_entry(candidate, entry, slot)
    _rebuild_add_prop_list(candidate)
    return candidate


def _score_plan(
    role_detail: RoleDetailData,
    entries: Sequence[EchoEntry],
    slots: tuple[int, ...],
    sum_damage: Callable[[RoleDetailData], float],
) -> float:
    return sum_damage(_build_candidate(role_detail, entries, slots))


def _score_baseline(role_detail: RoleDetailData, sum_damage: Callable[[RoleDetailData], float]) -> float:
    return sum_damage(copy.deepcopy(role_detail))


async def compare_phantoms(
    role_detail: RoleDetailData,
    entries: Sequence[EchoEntry],
    sum_damage: Callable[[RoleDetailData], float],
    pool: ThreadPoolExecutor = _COMPARE_POOL,
) -> ReplaceResult:
    """联合枚举同 COST 槽位组合，选择完整替换后伤害最高的方案。"""
    entries = tuple(entries)
    plans = _replacement_plans(role_detail, entries)
    loop = asyncio.get_running_loop()
    baseline = loop.run_in_executor(pool, _score_baseline, role_detail, sum_damage)
    values = await asyncio.gather(
        baseline,
        *(loop.run_in_executor(pool, partial(_score_plan, role_detail, entries, plan, sum_damage)) for plan in plans),
    )
    scores = values[1:]
    best_index = max(range(len(plans)), key=scores.__getitem__)
    best_slots = plans[best_index]
    candidate = await loop.run_in_executor(pool, _build_candidate, role_detail, entries, best_slots)
    return ReplaceResult(candidate, best_slots, scores[best_index], values[0], len(plans))
