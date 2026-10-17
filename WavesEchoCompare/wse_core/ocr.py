"""声骸截图 OCR 识别 (rapidocr 懒加载, to_thread 执行, 多 pass 抗模糊)。"""

import threading
from dataclasses import dataclass

from PIL import Image

from gsuid_core.pool import to_thread
from gsuid_core.logger import logger


class OcrUnavailableError(Exception):
    """rapidocr 未安装或初始化失败。"""


@dataclass
class OcrLine:
    text: str
    score: float
    x: int
    y: int
    h: int
    w: int = 0


_engine = None
_engine_failed = False
_engine_lock = threading.Lock()


def _get_engine():
    global _engine, _engine_failed
    if _engine is not None:
        return _engine
    if _engine_failed:
        raise OcrUnavailableError("[鸣潮·声骸对比] OCR 组件初始化失败, 请检查 rapidocr-onnxruntime 是否安装完整")
    from rapidocr_onnxruntime import RapidOCR

    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            _engine = RapidOCR()
        except Exception as e:
            _engine_failed = True
            logger.warning(f"[鸣潮·声骸对比] RapidOCR 初始化失败: {e}")
            raise OcrUnavailableError(
                "[鸣潮·声骸对比] OCR 组件初始化失败, 请检查 rapidocr-onnxruntime 是否安装完整"
            ) from e
    return _engine


_infer_lock = threading.Lock()


def _run_once(im: Image.Image) -> list[OcrLine]:
    engine = _get_engine()
    import numpy as np

    arr = np.array(im.convert("RGB"))
    # RapidOCR 内部非全线程安全, 推理段串行化 (单次推理很快, 无并发瓶颈)
    with _infer_lock:
        raw, _elapse = engine(arr)
    lines: list[OcrLine] = []
    if not raw:
        return lines
    for box, text, score in raw:
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        lines.append(
            OcrLine(
                text=str(text),
                score=float(score),
                x=min(xs),
                y=min(ys),
                h=max(ys) - min(ys),
                w=max(xs) - min(xs),
            )
        )
    return lines


_PASS_SCALES = (2.0, 1.5, 3.0)
_EXTRA_SCALES = (1.5, 3.0)


def _merge_lines(line_groups: list[list[OcrLine]], scales: list[float]) -> list[OcrLine]:
    """合并多个 pass 的行: 坐标归一化 → 同行拼接/取优。"""
    normalized: list[OcrLine] = []
    for idx, p in enumerate(line_groups):
        scale = scales[idx] if idx < len(scales) else 1.0
        group: list[OcrLine] = []
        for ln in p:
            if scale == 1.0:
                group.append(ln)
            else:
                group.append(
                    OcrLine(
                        text=ln.text,
                        score=ln.score,
                        x=int(ln.x / scale),
                        y=int(ln.y / scale),
                        h=max(int(ln.h / scale), 1),
                        w=int(ln.w / scale),
                    )
                )
        normalized.extend(merge_card_rows(group))

    merged: list[OcrLine] = []
    # 跨 pass 去重只比较同一文本框，不把相邻的两条词条合并。
    for line in sorted(normalized, key=lambda item: (item.y, item.x)):
        matched: int | None = None
        for i, previous in enumerate(merged):
            distance = abs(line.y + line.h / 2 - previous.y - previous.h / 2)
            overlap = line.x < previous.x + previous.w and previous.x < line.x + line.w
            if distance <= max(3, min(line.h, previous.h) * 0.5) and overlap:
                matched = i
                break
        if matched is None:
            merged.append(line)
        elif (line.score, len(line.text)) > (merged[matched].score, len(merged[matched].text)):
            merged[matched] = line
    return merge_card_rows(merged)


def merge_card_rows(lines: list[OcrLine]) -> list[OcrLine]:
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda item: (item.y + item.h / 2, item.x)):
        if rows:
            previous = rows[-1][0]
            distance = abs(line.y + line.h / 2 - previous.y - previous.h / 2)
            if distance <= max(3, min(line.h, previous.h) * 0.5):
                rows[-1].append(line)
                continue
        rows.append([line])
    merged: list[OcrLine] = []
    for row in rows:
        row.sort(key=lambda item: item.x)
        left, top = min(item.x for item in row), min(item.y for item in row)
        right = max(item.x + item.w for item in row)
        bottom = max(item.y + item.h for item in row)
        merged.append(
            OcrLine(
                " ".join(item.text for item in row),
                min(item.score for item in row),
                left,
                top,
                bottom - top,
                right - left,
            )
        )
    return merged


def _run_scaled(im: Image.Image, scale: float) -> list[OcrLine]:
    w, h = int(im.size[0] * scale), int(im.size[1] * scale)
    up = im.resize((w, h), Image.Resampling.LANCZOS)
    return _run_once(up)


@to_thread
def run_ocr_layout(im: Image.Image, scale: float = 1.0) -> list[OcrLine]:
    """保留独立文本框，供卡片分区使用。"""
    lines = _run_once(im) if scale == 1.0 else _run_scaled(im, scale)
    return [
        OcrLine(
            line.text,
            line.score,
            int(line.x / scale),
            int(line.y / scale),
            max(1, int(line.h / scale)),
            int(line.w / scale),
        )
        for line in lines
    ]


@to_thread
def run_ocr_fast(im: Image.Image) -> list[OcrLine]:
    """快速识别: 原图 + 2x 两个 pass (清晰截图足够)。"""
    groups: list[list[OcrLine]] = [_run_once(im)]
    scales: list[float] = [1.0]
    try:
        groups.append(_run_scaled(im, 2.0))
        scales.append(2.0)
    except Exception as e:
        logger.debug(f"[鸣潮·声骸对比] 2x 放大 pass 失败: {e}")
    return _merge_lines(groups, scales)


@to_thread
def run_ocr_extra(im: Image.Image) -> list[OcrLine]:
    """补救识别: 快速结果 + 1.5x + 3x 合并 (模糊/漏识别时用)。"""
    groups: list[list[OcrLine]] = [_run_once(im)]
    scales: list[float] = [1.0]
    for scale in _EXTRA_SCALES:
        try:
            groups.append(_run_scaled(im, scale))
            scales.append(scale)
        except Exception as e:
            logger.debug(f"[鸣潮·声骸对比] {scale}x 放大 pass 失败, 跳过: {e}")
    return _merge_lines(groups, scales)
