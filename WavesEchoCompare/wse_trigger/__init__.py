"""声骸截图对比触发器: ww<角色名>提升[cost] + 声骸截图。"""

import asyncio

import httpx
from PIL import Image

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.pool import to_thread
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.api.model import RoleDetailData

from ..wse_core.ocr import OcrUnavailableError
from ..wse_core.batch import ImageInputError, image_urls, read_images
from ..wse_core.damage import delta_text, sum_expected_damage
from ..wse_core.parser import EchoParseError
from ..wse_core.replace import ReplaceError, compare_phantoms

PATTERN = r"[\u4e00-\u9fa5a-zA-Z0-9]{1,12}"
sv_waves_echo_compare = SV("waves声骸对比", priority=4)


@sv_waves_echo_compare.on_regex(
    rf"^(?P<char>{PATTERN})提升预估(?P<cost>[1-4])?$",
    block=True,
)
async def echo_compare(bot: Bot, ev: Event) -> list[str] | None:
    """ww<角色>提升预估[cost]: cost 可选, 缺省时由截图识别。

    「提升预估」字面不与 XW 的「提升」(优化建议) 命令冲突。
    """
    char = ev.regex_dict["char"] if "char" in ev.regex_dict else None
    if not char:
        return
    raw_cost = ev.regex_dict["cost"] if "cost" in ev.regex_dict else None
    cmd_cost = int(raw_cost) if raw_cost else None

    urls = image_urls(ev)
    if not urls:
        return await bot.send("[鸣潮] 请同时发送声骸截图, 例: ww长离提升预估3 + 声骸详情图")

    from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.name_convert import (
        alias_to_char_name,
        char_name_to_char_id,
    )
    from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.char_info_utils import (
        get_char_detail_for_id,
    )
    from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.utils.database.models import (
        WavesBind,
    )

    uid = await WavesBind.get_uid_by_game(ev.user_id, ev.bot_id)
    if not uid:
        return await bot.send("[鸣潮] 您还未绑定鸣潮特征码, 请使用【ww绑定uid】完成绑定！")

    char_name = alias_to_char_name(char)
    char_id = char_name_to_char_id(char_name)
    if not char_id or len(char_id) != 4 or not char_id.isdigit():
        return await bot.send(f"未找到角色【{char}】, 请检查输入")

    role_detail = await get_char_detail_for_id(uid, char_id)
    if role_detail is None:
        return await bot.send(f"[鸣潮] 未找到【{char_name}】面板数据, 请先使用 ww刷新{char_name}面板")

    try:
        recognized = await read_images(urls, cmd_cost)
    except EchoParseError as exc:
        return await bot.send(str(exc))
    except OcrUnavailableError as exc:
        return await bot.send(str(exc))
    except ImageInputError as exc:
        return await bot.send(str(exc))
    except httpx.HTTPError as exc:
        logger.warning(f"[鸣潮·声骸对比] 截图下载失败: {exc}")
        return await bot.send("[鸣潮] 声骸截图下载失败，请重试")

    entries = [item.entry for item in recognized]
    try:
        result = await compare_phantoms(role_detail, entries, sum_expected_damage)
    except ReplaceError as exc:
        return await bot.send(str(exc))
    except RuntimeError as exc:
        logger.warning(f"[鸣潮·声骸对比] 伤害计算不可用: {exc}")
        return await bot.send("[鸣潮] 伤害计算模块暂不可用，请稍后再试")
    costs = "/".join(str(entry.cost) for entry in entries)
    text = delta_text(char_name, costs, result.before, result.damage_sum)
    if result.before <= 0:
        return await bot.send(text)
    if result.damage_sum < result.before:
        text += "\n已选择全部替换方案中伤害降幅最小的组合。"
    for item, slot in zip(recognized, result.slot_indices, strict=True):
        entry = item.entry
        text += f"\n{item.source}：COST {entry.cost} → 槽位 {slot + 1}，{entry.main_name}{entry.main_value}"
        if entry.partial:
            text += f"（副词条仅识别 {len(entry.subs)}/5 条，请核对）"
    text += "\n默认与目标槽位同套装，保留原声骸本体与套装。"

    # 只渲染原面板与最终胜出方案，不给中间候选出图。
    im_left, im_right = await asyncio.gather(
        draw_panel(ev, uid, char_name, role_detail),
        draw_panel(ev, uid, char_name, result.role_detail),
    )
    if not isinstance(im_left, Image.Image) or not isinstance(im_right, Image.Image):
        return await bot.send(text)

    merged = await _concat_pk_images(im_left, im_right)
    from gsuid_core.segment import MessageSegment
    from gsuid_core.utils.image.convert import convert_img

    # 文本段 + 图片段列表发送 (MessageSegment 不能嵌进 f-string)
    await bot.send(
        [
            MessageSegment.text(f"{text}\n(左:替换前 / 右:替换后)"),
            MessageSegment.image(await convert_img(merged)),
        ]
    )


@to_thread
def _concat_pk_images(im1: Image.Image, im2: Image.Image) -> Image.Image:
    new_im = Image.new("RGBA", (im1.size[0] + im2.size[0], max(im1.size[1], im2.size[1])))
    new_im.paste(im1, (0, 0))
    new_im.paste(im2, (im1.size[0], 0))
    return new_im


async def draw_panel(ev: Event, uid: str, char_name: str, role_detail: RoleDetailData) -> Image.Image | bytes | str:
    """复用 XW 渲染面板 (role_detail_override 注入替换前/后数据)。

    change_list_regex='对比' 为哨兵: 跳过 XW record_view/建议副作用, 且不命中
    ChangeParser 任何前缀 → role_detail 原样保留。
    """
    from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.wutheringwaves_charinfo.draw_char_card import (
        draw_char_detail_img,
    )

    im = await draw_char_detail_img(
        ev,
        uid,
        char_name,
        ev.user_id,
        need_convert_img=False,
        change_list_regex="对比",
        role_detail_override=role_detail,
    )
    return im
