"""声骸截图 OCR 识别 (rapidocr 懒加载, to_thread 执行, 双 pass 抗模糊)。"""

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


_engine = None
_engine_failed = False
_engine_lock = None


def _get_engine():
    global _engine, _engine_failed, _engine_lock
    if _engine is not None:
        return _engine
    if _engine_failed:
        raise OcrUnavailableError("[鸣潮·声骸对比] OCR 组件初始化失败, 请检查 rapidocr-onnxruntime 是否安装完整")
    import threading

    if _engine_lock is None:
        _engine_lock = threading.Lock()
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


_infer_lock = None


def _run_once(im: Image.Image) -> list[OcrLine]:
    global _infer_lock
    import threading

    engine = _get_engine()
    import numpy as np

    arr = np.array(im.convert("RGB"))
    # RapidOCR 内部非全线程安全, 推理段串行化 (单次推理很快, 无并发瓶颈)
    if _infer_lock is None:
        _infer_lock = threading.Lock()
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
            )
        )
    return lines


@to_thread
def run_ocr(im: Image.Image) -> list[OcrLine]:
    """双 pass 识别: 原图 + 2x LANCZOS 放大 (拍屏模糊/摩尔纹补偿), 行合并。

    合并规则: 按 Y 聚簇, 每簇取文本最长的一行 (模糊图常漏识别行, 补全优先)。
    """
    first = _run_once(im)
    try:
        up = im.resize((im.size[0] * 2, im.size[1] * 2), Image.Resampling.LANCZOS)
        second = _run_once(up)
    except Exception as e:
        logger.debug(f"[鸣潮·声骸对比] 2x 放大 pass 失败, 仅用原图: {e}")
        second = []
    if not second:
        return first
    if not first:
        return second

    # 2x 图的坐标缩回原图尺度再合并
    scaled = [OcrLine(text=ln.text, score=ln.score, x=ln.x // 2, y=ln.y // 2, h=ln.h // 2) for ln in second]
    merged: list[OcrLine] = []
    all_lines = sorted([*first, *scaled], key=lambda ln: ln.y)
    for ln in all_lines:
        if not merged:
            merged.append(ln)
            continue
        prev = merged[-1]
        if abs(ln.y - prev.y) <= max(prev.h, ln.h, 12):
            # 同一文本行: 保留文本更长 (信息更全) 的一行
            if len(ln.text) > len(prev.text):
                merged[-1] = ln
        else:
            merged.append(ln)
    return merged
