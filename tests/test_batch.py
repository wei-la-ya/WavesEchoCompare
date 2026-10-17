import io
import asyncio

import httpx
import pytest
from PIL import Image
from wse_core import batch
from wse_core.parser import EchoEntry, EchoParseError

from gsuid_core.models import Event, Message


def test_collects_all_images_in_order_and_avoids_repeated_url() -> None:
    event = Event()
    event.content = [
        Message("text", "命令"),
        Message("img", "https://a/1"),
        Message("image", "https://a/2"),
        Message("img", "https://a/1"),
    ]
    assert batch.image_urls(event) == ["https://a/1", "https://a/2"]


def test_legacy_single_image_fallback() -> None:
    event = Event()
    event.image = "https://a/1"
    assert batch.image_urls(event) == ["https://a/1"]


@pytest.mark.parametrize("fail", [False, True])
def test_batch_keeps_image_and_card_order_or_reports_failure(monkeypatch: pytest.MonkeyPatch, fail: bool) -> None:
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        width = 10 if request.url.path == "/1" else 20
        output = io.BytesIO()
        Image.new("RGB", (width, 10)).save(output, format="PNG")
        return httpx.Response(200, content=output.getvalue())

    def client_factory(*, timeout: int) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(handler), timeout=timeout)

    async def recognize(image: Image.Image, cmd_cost: int | None) -> list[EchoEntry]:
        if image.width == 10:
            await asyncio.sleep(0.01)
            return [EchoEntry(3, "攻击", "30%"), EchoEntry(1, "攻击", "18%")]
        if fail:
            raise EchoParseError("主词条模糊")
        return [EchoEntry(4, "暴击", "22%")]

    monkeypatch.setattr(batch.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(batch, "recognize_echoes", recognize)
    if fail:
        with pytest.raises(EchoParseError, match="第 2 张图片.*主词条模糊"):
            asyncio.run(batch.read_images(["https://a/1", "https://a/2"], None))
    else:
        result = asyncio.run(batch.read_images(["https://a/1", "https://a/2"], None))
        assert [item.entry.cost for item in result] == [3, 1, 4]
        assert [item.source for item in result] == ["图1/声骸1", "图1/声骸2", "图2/声骸1"]
