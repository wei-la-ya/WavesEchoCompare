"""收集消息中的图片，并保留每只声骸的来源。"""

import io
import asyncio
from dataclasses import dataclass

import httpx
from PIL import Image

from gsuid_core.pool import to_thread
from gsuid_core.models import Event

from .parser import EchoEntry, EchoParseError
from .recognition import recognize_echoes

_IMAGE_CACHE: dict[str, Image.Image] = {}


@dataclass(frozen=True)
class RecognizedEcho:
    source: str
    entry: EchoEntry


class ImageInputError(Exception):
    """下载内容无法解码为图片。"""


def image_urls(ev: Event) -> list[str]:
    urls: list[str] = []
    for content in ev.content:
        if content.type in ("img", "image") and isinstance(content.data, str):
            url = content.data
            if url.startswith(("https://", "http://")) and url not in urls:
                urls.append(url)
    if not urls and ev.image:
        urls.append(ev.image)
    return urls


@to_thread
def _decode_image(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGB")


async def read_images(urls: list[str], cmd_cost: int | None) -> list[RecognizedEcho]:
    if len(urls) > 5:
        raise EchoParseError("[鸣潮] 每次最多发送 5 张声骸截图")
    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=20) as client:

        async def read_one(index: int, url: str) -> list[RecognizedEcho]:
            async with semaphore:
                if url in _IMAGE_CACHE:
                    image = _IMAGE_CACHE[url]
                else:
                    response = await client.get(url)
                    response.raise_for_status()
                    try:
                        image = await _decode_image(response.content)
                    except OSError as exc:
                        raise ImageInputError(f"[鸣潮] 第 {index} 张图片无法读取，请重新发送截图") from exc
                    if len(_IMAGE_CACHE) >= 16:
                        del _IMAGE_CACHE[next(iter(_IMAGE_CACHE))]
                    _IMAGE_CACHE[url] = image
                try:
                    entries = await recognize_echoes(image, cmd_cost)
                except EchoParseError as exc:
                    raise EchoParseError(f"第 {index} 张图片：{exc}") from exc
                return [RecognizedEcho(f"图{index}/声骸{card}", entry) for card, entry in enumerate(entries, 1)]

        groups = await asyncio.gather(
            *(read_one(index, url) for index, url in enumerate(urls, 1)), return_exceptions=True
        )
    results: list[RecognizedEcho] = []
    for group in groups:
        if isinstance(group, BaseException):
            raise group
        results.extend(group)
    if len(results) > 5:
        raise EchoParseError(f"[鸣潮] 共识别到 {len(results)} 个声骸，每次最多替换 5 个，请减少截图")
    return results
