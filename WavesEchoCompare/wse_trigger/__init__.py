"""声骸截图对比触发器: ww<角色名>提升[cost] + 声骸截图。"""

import io
import asyncio
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import httpx
from PIL import Image

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from ..wse_core.ocr import OcrLine, run_ocr_fast, run_ocr_extra
from ..wse_core.damage import delta_text, sum_expected_damage
from ..wse_core.parser import EchoParseError, parse_echo_lines
from ..wse_core.replace import ReplaceError as ReplaceError, replace_phantom

PATTERN = r"[\u4e00-\u9fa5a-zA-Z0-9]{1,12}"
sv_waves_echo_compare = SV("waves声骸对比", priority=4)

_image_cache: dict[str, Image.Image] = {}
_image_lock = asyncio.Lock()


def _pick_image_url(ev: Event) -> Optional[str]:
    for content in ev.content:
        if content.type in ("img", "image") and content.data and isinstance(content.data, str):
            if content.data.startswith("http"):
                return content.data
    return ev.image if ev.image else None


async def _download_image(url: str) -> Image.Image:
    async with _image_lock:
        cached = _image_cache.get(url)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(url)
        res.raise_for_status()
    # load() + copy(): 脱离 BytesIO 惰性加载, 避免缓存对象被并发读取时 buffer 失效
    with Image.open(io.BytesIO(res.content)) as src:
        src.load()
        im = src.copy()
    async with _image_lock:
        if len(_image_cache) > 16:
            _image_cache.clear()
        _image_cache[url] = im
    return im


@sv_waves_echo_compare.on_regex(
    (
        rf"^(?P<char>{PATTERN})提升(?P<cost>[1-4])$",
        rf"^(?P<char>{PATTERN})(?P<alias>声骸比一比|比一比)$",
    ),
    block=True,
)
async def echo_compare(bot: Bot, ev: Event):
    """入口一: ww<角色>提升[cost] (cost 必填, 避免截胡 XW 的「提升」优化建议命令)。

    入口二: ww<角色>声骸比一比 / ww<角色>比一比 (cost 由截图识别)。
    """
    char = ev.regex_dict.get("char")
    if not char:
        return
    raw_cost = ev.regex_dict.get("cost")
    cmd_cost = int(raw_cost) if raw_cost else None

    url = _pick_image_url(ev)
    if url is None:
        return await bot.send("[鸣潮] 请同时发送声骸截图, 例: ww长离提升3 + 声骸详情图, 或 ww长离声骸比一比 + 截图")

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
        im = await _download_image(url)
    except Exception as e:
        logger.warning(f"[鸣潮·声骸对比] 截图下载失败: {e}")
        return await bot.send("[鸣潮] 声骸截图下载失败, 请重试")

    try:
        # 快速路径: 1x+2x 两 pass; 解析出的副词条不足 5 条时再跑 1.5x+3x 补救
        lines: list[OcrLine] = await run_ocr_fast(im)
        entry = parse_echo_lines(lines, cmd_cost)
        if entry.partial:
            logger.info("[鸣潮·声骸对比] 快速识别副词条不足, 启动补救 pass")
            lines = await run_ocr_extra(im)
            entry = parse_echo_lines(lines, cmd_cost)
    except EchoParseError as e:
        return await bot.send(str(e))

    # 基线伤害与双槽位择优互相独立, 线程池并行 (WuWaCalc 为 C 扩展, GIL 已释放)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            before_future = pool.submit(sum_expected_damage, role_detail)
            result = replace_phantom(role_detail, entry, sum_expected_damage, pool)
            before = before_future.result()
    except ReplaceError as e:
        return await bot.send(str(e))
    except RuntimeError as e:
        # XW WuWaCalc 占位类在计算模块未下载完成时抛 RuntimeError
        logger.warning(f"[鸣潮·声骸对比] 伤害计算不可用: {e}")
        return await bot.send("[鸣潮] 伤害计算模块暂不可用 (可能正在下载), 请稍后再试")
    after = result.damage_sum

    # 渲染左右面板与拼接互相独立, 并行执行
    im_left, im_right = await asyncio.gather(
        draw_panel(ev, uid, char_name, role_detail),
        draw_panel(ev, uid, char_name, result.role_detail),
    )
    if not isinstance(im_left, Image.Image) or not isinstance(im_right, Image.Image):
        return await bot.send(delta_text(char_name, entry.cost, before, after, entry.partial, len(entry.subs)))

    merged = _concat_pk_images(im_left, im_right)
    from gsuid_core.segment import MessageSegment
    from gsuid_core.utils.image.convert import convert_img

    # 文本段 + 图片段列表发送 (MessageSegment 不能嵌进 f-string)
    text = delta_text(char_name, entry.cost, before, after, entry.partial, len(entry.subs))
    await bot.send(
        [
            MessageSegment.text(f"{text}\n(左:替换前 / 右:替换后)"),
            MessageSegment.image(await convert_img(merged)),
        ]
    )


def _concat_pk_images(im1: Image.Image, im2: Image.Image) -> Image.Image:
    new_im = Image.new("RGBA", (im1.size[0] + im2.size[0], max(im1.size[1], im2.size[1])))
    new_im.paste(im1, (0, 0))
    new_im.paste(im2, (im1.size[0], 0))
    return new_im


async def draw_panel(ev: Event, uid: str, char_name: str, role_detail):
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
