import pytest
from PIL import Image, UnidentifiedImageError

from mc_observatory.vision import _decoded_image, image
from test_holo import PixelClient, PNG
from mc_observatory.holo_tools import ToolExecutor


def test_pixel_decode_is_reused_but_returned_images_are_independent(monkeypatch):
    _decoded_image.cache_clear()
    opened = []
    original = Image.open

    def load(*args, **kwargs):
        opened.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(Image, "open", load)
    first = image(PNG)
    first.putpixel((0, 0), (0, 0, 0))
    second = image(PNG)
    assert second.getpixel((0, 0)) == (90, 100, 110)
    assert first is not second
    assert len(opened) == 1
    assert _decoded_image.cache_info().maxsize == 4


@pytest.mark.parametrize("invalid", [b"", b"not a PNG"])
def test_invalid_pixels_do_not_poison_decode_cache(invalid):
    with pytest.raises(UnidentifiedImageError):
        image(invalid)
    assert image(PNG).size == (640, 480)


async def test_still_close_check_frame_is_reused_for_probe_reference(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda *_: False)

    class Executor(ToolExecutor):
        async def approach_close(self):
            await self.observe()
            return True

        async def primitive(self, kind, **args):
            if kind != "wait":
                await super().primitive(kind, **args)

    ex = Executor(PixelClient(), 640, 480, jev=object())
    ex.begin_step(PNG)
    result = await ex.execute({"name": "attack_until_broken", "arguments": {"max_s": 2}}, "skills")
    assert result["outcome"] == "not_in_reach"
    assert result["timings"]["screenshot_count"] == 3
