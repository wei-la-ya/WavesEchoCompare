"""伤害求和与对比文案 (全部伤害类型期望伤害求和, 与 XW 面板同计算路径)。"""

import copy

from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.calc import WuWaCalc
from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.api.model import (
    RoleDetailData,
)
from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.api.model_other import (
    EnemyDetailData,
)
from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.damage.abstract import (
    DamageDetailRegister,
)


def sum_expected_damage(role_detail: RoleDetailData) -> float:
    """全部伤害类型期望伤害求和; 无伤害模块或无声骸返回 0。"""
    from gsuid_core.logger import logger

    detail = DamageDetailRegister.find_class(str(role_detail.role.roleId))
    if not detail:
        logger.warning("[鸣潮·声骸对比] 角色 {} 无伤害计算模块, 伤害求和返回 0", role_detail.role.roleId)
        return 0.0
    if not (role_detail.phantomData and role_detail.phantomData.equipPhantomList):
        return 0.0
    calc = WuWaCalc(role_detail, EnemyDetailData())
    calc.phantom_pre = calc.prepare_phantom()
    calc.phantom_card = calc.enhance_summation_phantom_value(calc.phantom_pre)
    calc.role_card = calc.enhance_summation_card_value(calc.phantom_card)
    calc.damageAttribute = calc.card_sort_map_to_attribute(calc.role_card)
    total = 0.0
    for dt in detail:
        attr = copy.deepcopy(calc.damageAttribute)
        setattr(attr, "_log_title", dt["title"])
        _crit, expected = dt["func"](attr, role_detail)
        try:
            total += float(str(expected).replace(",", ""))
        except (TypeError, ValueError):
            continue
    logger.info(
        "[鸣潮·声骸对比] {} 伤害求和={} 暴击={} 暴伤={} 攻击={}",
        role_detail.role.roleName,
        round(total, 1),
        calc.role_card.get("暴击"),
        calc.role_card.get("暴击伤害"),
        calc.role_card.get("攻击"),
    )
    return total


def delta_text(
    char_name: str,
    cost: int | str,
    before: float,
    after: float,
    partial: bool = False,
    sub_count: int = 0,
) -> str:
    """回复文案: 伤害提升/降低了约 xx%; partial 时附加核对提醒。"""
    if before <= 0:
        return f"[鸣潮] {char_name} cost{cost} 替换对比失败: 该角色暂无伤害计算数据, 请检查计算模块是否下载完成"
    pct = (after - before) / before * 100
    if pct >= 0:
        head = f"[鸣潮] {char_name} cost{cost} 替换后伤害提升了约 {pct:.1f}%"
    else:
        head = f"[鸣潮] {char_name} cost{cost} 替换后伤害降低了约 {abs(pct):.1f}%"
    if partial:
        head += f"\n⚠️ 副词条仅识别到 {sub_count}/5 条, 有词条被漏识别, 结果仅供参考, 建议换清晰截图重试"
    return head
