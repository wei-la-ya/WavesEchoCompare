"""声骸截图 OCR 识别 (rapidocr 懒加载, to_thread 执行, 多 pass 抗模糊)。"""

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
                w=max(xs) - min(xs),
            )
        )
    return lines


_PASS_SCALES = (2.0, 1.5, 3.0)


@to_thread
def run_ocr(im: Image.Image) -> list[OcrLine]:
    """多 pass 识别: 原图 + 2x + 1.5x + 3x LANCZOS 放大 (拍屏模糊/摩尔纹补偿), 行合并。

    合并规则: 同一视觉行且 x 重叠 → 取文本更长行; x 不重叠 (名称/数值分列) → 拼接。
    3x pass 补救大图中深色底小字名称行的漏识别。
    """
    passes: list[list[OcrLine]] = [_run_once(im)]
    for scale in _PASS_SCALES:
        try:
            w, h = int(im.size[0] * scale), int(im.size[1] * scale)
            up = im.resize((w, h), Image.Resampling.LANCZOS)
            passes.append(_run_once(up))
        except Exception as e:
            logger.debug(f"[鸣潮·声骸对比] {scale}x 放大 pass 失败, 跳过: {e}")

    normalized: list[OcrLine] = []
    for idx, p in enumerate(passes):
        scale = _PASS_SCALES[idx - 1] if idx > 0 else 1.0
        for ln in p:
            if scale == 1.0:
                normalized.append(ln)
            else:
                normalized.append(
                    OcrLine(
                        text=ln.text,
                        score=ln.score,
                        x=int(ln.x / scale),
                        y=int(ln.y / scale),
                        h=max(int(ln.h / scale), 1),
                        w=int(ln.w / scale),
                    )
                )

    merged: list[OcrLine] = []
    all_lines = sorted(normalized, key=lambda ln: (ln.y, ln.x))
    for ln in all_lines:
        if not merged:
            merged.append(ln)
            continue
        prev = merged[-1]
        same_row = abs(ln.y - prev.y) <= max(prev.h, ln.h, 12)
        # x 区间相交判定 (双向): [ln.x, ln.x+ln.w] ∩ [prev.x, prev.x+prev.w] 非空
        overlaps = ln.x <= prev.x + prev.w and prev.x <= ln.x + ln.w
        if same_row and overlaps:
            # 同一视觉行且 x 重叠: 保留文本更长 (信息更全) 的一行
            if len(ln.text) > len(prev.text):
                merged[-1] = ln
        elif same_row:
            # 同一视觉行但 x 分列 (名称/数值两段布局): 拼接
            merged[-1] = OcrLine(
                text=f"{prev.text} {ln.text}",
                score=min(prev.score, ln.score),
                x=min(prev.x, ln.x),
                y=min(prev.y, ln.y),
                h=max(prev.h, ln.h),
                w=max(prev.x + prev.w, ln.x + ln.w) - min(prev.x, ln.x),
            )
        else:
            merged.append(ln)
    return merged
