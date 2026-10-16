"""替换面板声骸词条; cost3/cost1 双槽位取伤害更优者。"""

import copy
from dataclasses import dataclass

from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.api.model import (
    Props,
    RoleDetailData,
)

from .parser import MAIN_VALUE


class ReplaceError(Exception):
    """替换失败, msg 是给用户看的文案。"""


def _cost_slots(role_detail: RoleDetailData, cost: int) -> list[int]:
    """面板 equipPhantomList 中该 cost 的槽位下标列表。"""
    pd = role_detail.phantomData
    eq = pd.equipPhantomList if pd else None
    if not eq:
        return []
    return [i for i, ep in enumerate(eq) if ep and ep.cost == cost]


def _apply_entry(role_detail: RoleDetailData, entry, slot_index: int) -> None:
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
        main_name = f"{role_detail.role.attributeName}伤害加成"
    main_value = MAIN_VALUE[entry.main_name][entry.cost]
    props = [Props(attributeName=main_name, attributeValue=main_value)]
    if entry.main2_name is not None and entry.main2_value is not None:
        props.append(Props(attributeName=entry.main2_name, attributeValue=entry.main2_value))
    slot.mainProps = props
    slot.subProps = [Props(attributeName=name, attributeValue=value) for name, value in entry.subs]


@dataclass
class ReplaceResult:
    """择优结果: 替换后面板 + 选定槽位 + 该面板伤害求和 (择优时已算)。"""

    role_detail: RoleDetailData
    slot_index: int
    damage_sum: float


def replace_phantom(
    role_detail: RoleDetailData,
    entry,
    sum_damage,
) -> ReplaceResult:
    """在所有同 cost 槽位上试替换, 取伤害求和更高者。

    sum_damage: Callable[[RoleDetailData], float] — 伤害求和函数 (damage.py 提供)。
    """
    from gsuid_core.logger import logger

    slots = _cost_slots(role_detail, entry.cost)
    if not slots:
        raise ReplaceError(f"[鸣潮] 面板上没有 cost{entry.cost} 声骸槽位")

    best: tuple[float, int, RoleDetailData] | None = None
    for idx in slots:
        candidate = copy.deepcopy(role_detail)
        _apply_entry(candidate, entry, idx)
        score = sum_damage(candidate)
        if best is None or score > best[0]:
            best = (score, idx, candidate)
    assert best is not None
    # 诊断: 替换生效性自证 — 输出替换槽位前后的词条与关键面板属性
    old_pd = role_detail.phantomData
    new_pd = best[2].phantomData
    old_eq = old_pd.equipPhantomList if old_pd else None
    new_eq = new_pd.equipPhantomList if new_pd else None
    old_slot = old_eq[best[1]] if old_eq and old_eq[best[1]] else None
    new_slot = new_eq[best[1]] if new_eq and new_eq[best[1]] else None
    logger.info(
        "[鸣潮·声骸对比] 替换槽位={} cost={} 主词条: {} -> {} | 副词条数: {} -> {}",
        best[1],
        entry.cost,
        [(m.attributeName, m.attributeValue) for m in (old_slot.mainProps or [])] if old_slot else [],
        [(m.attributeName, m.attributeValue) for m in (new_slot.mainProps or [])] if new_slot else [],
        len(old_slot.subProps or []) if old_slot else 0,
        len(new_slot.subProps or []) if new_slot else 0,
    )
    return ReplaceResult(role_detail=best[2], slot_index=best[1], damage_sum=best[0])
