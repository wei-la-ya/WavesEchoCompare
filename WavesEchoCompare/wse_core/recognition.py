"""一张图片中的声骸分区识别与定向补救。"""

from PIL import Image

from gsuid_core.pool import to_thread

from .ocr import run_ocr_fast, run_ocr_extra, run_ocr_layout, merge_card_rows
from .layout import CardRegion, find_card_regions
from .parser import EchoEntry, EchoParseError, _detect_cost, parse_echo_lines

_find_regions = to_thread(find_card_regions)


async def _read_region(im: Image.Image, region: CardRegion, cmd_cost: int | None) -> EchoEntry:
    best: EchoEntry | None = None
    error: EchoParseError | None = None
    cost_hint = _detect_cost(region.lines, cmd_cost)
    # OCR 属于不可信输入，解析失败时只补救该卡，不吞掉最终错误。
    try:
        best = parse_echo_lines(region.lines, cost_hint)
    except EchoParseError as exc:
        error = exc
    if best is not None and not best.partial and best.main2_value is not None:
        return best
    crop = im.crop(region.box)
    for runner in (run_ocr_fast, run_ocr_extra):
        lines = await runner(crop)
        try:
            entry = parse_echo_lines(lines, cost_hint)
        except EchoParseError as exc:
            error = exc
            continue
        if best is None or (len(entry.subs), entry.main2_value is not None) > (
            len(best.subs),
            best.main2_value is not None,
        ):
            best = entry
        if not best.partial and best.main2_value is not None:
            return best
    if best is not None:
        return best
    if error is not None:
        raise error
    raise EchoParseError("[鸣潮] 未识别到声骸详情，请发送清晰截图")


async def recognize_echoes(im: Image.Image, cmd_cost: int | None = None) -> list[EchoEntry]:
    lines = await run_ocr_layout(im)
    regions = await _find_regions(lines, im.width, im.height)
    if not regions or any(region.lines and min(line.h for line in region.lines) < 18 for region in regions):
        enlarged = await run_ocr_layout(im, 2.0)
        enlarged_regions = await _find_regions(enlarged, im.width, im.height)
        if len(enlarged_regions) >= len(regions):
            lines, regions = enlarged, enlarged_regions
    if len(regions) > 5:
        raise EchoParseError(f"[鸣潮] 图片中发现 {len(regions)} 个声骸，每次最多替换 5 个，请只截取需要替换的卡片")
    if not regions:
        regions = [CardRegion((0, 0, im.width, im.height), merge_card_rows(lines))]
    entries: list[EchoEntry] = []
    for index, region in enumerate(regions, 1):
        try:
            entries.append(await _read_region(im, region, cmd_cost))
        except EchoParseError as exc:
            raise EchoParseError(f"第 {index} 个声骸识别失败：{exc}") from exc
    return entries
