import io
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageDraw

from mc_observatory.holo import HoloAgent, HoloAPI, HoloAPIError, HoloConfig
from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.vision import outline, broken_caption, hotbar_change, _LETTERS
from test_holo import PixelClient, FakeAPI, PNG, call, response


def encode(im):
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def scene(rect=False, caption=False, item=False):
    im = Image.new("RGB", (854, 480), (90, 100, 110))
    d = ImageDraw.Draw(im)
    if rect:
        d.rectangle((380, 190, 475, 290), outline="black", width=2)
    if caption:
        x = 714
        for ch in "Block broken":
            if ch == " ":
                x += 8
                continue
            for y, row in enumerate(_LETTERS[ch]):
                for xx, bit in enumerate(row):
                    if bit == "1":
                        d.rectangle((x + xx*2, 390+y*2, x+xx*2+1, 391+y*2), fill="white")
            x += (len(_LETTERS[ch][0]) + 1) * 2
    if item:
        for x in range(255, 279, 4):
            for y in range(444, 468, 4):
                d.rectangle((x, y, x+2, y+2), fill="white")
    return encode(im)


def test_screenshot_cues():
    empty, outlined, broken, picked = scene(), scene(rect=True), scene(caption=True), scene(item=True)
    assert not outline(empty) and outline(outlined)
    assert broken_caption(empty) == 0 and broken_caption(broken) == 255
    assert not hotbar_change(empty, empty) and hotbar_change(empty, picked)
    with pytest.raises(ValueError, match="dimensions"):
        hotbar_change(empty, PNG)
    with pytest.raises(OSError):
        outline(b"bad")


@pytest.mark.parametrize("name,expected", [("outline_bark_oblique.png", True), ("outline_leaves.png", False), ("outline_distant_tree.png", False)])
def test_real_outline_frames(name, expected):
    assert outline((Path(__file__).parent / "fixtures" / name).read_bytes()) is expected


@pytest.mark.parametrize("box", [(399, 209, 542, 277), (383, 162, 546, 302)])
def test_outline_rejects_wide_background_and_crop_boundary_edges(box):
    im = Image.new("RGB", (854, 480), (90, 100, 110))
    ImageDraw.Draw(im).rectangle(box, outline="black", width=2)
    assert not outline(encode(im))


async def test_complete_narrow_face_stops_approach_before_movement():
    im = Image.new("RGB", (854, 480), (90, 100, 110))
    ImageDraw.Draw(im).rectangle((392, 145, 462, 335), outline="black", width=2)
    frame = encode(im)
    assert outline(frame)
    ex = ToolExecutor(PixelClient(), 854, 480)
    ex.begin_step(frame)
    assert await ex.reflex("approach_until_outline", .2) == "outline_pixel_cue"
    assert ex.actions == []
    assert not ex.held_keys
    ImageDraw.Draw(im).rectangle((389, 180, 395, 220), fill=(90, 100, 110))
    assert not outline(encode(im))


@pytest.mark.parametrize("name,cue,outcome", [
    ("approach_until_outline", "outline", "outline_pixel_cue"),
    ("mine", "broken_caption", "block_gone_pixel_cue"),
    ("collect", "hotbar_change", "hotbar_changed"),
])
async def test_reflex_early_exit_and_release(monkeypatch, name, cue, outcome):
    clock = [0.]
    monkeypatch.setattr("mc_observatory.holo_tools.time", type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])}))
    readings = iter([0, 255]) if cue == "broken_caption" else iter([False, True])
    monkeypatch.setattr(f"mc_observatory.holo_tools.{cue}", lambda *args: next(readings))
    class Executor(ToolExecutor):
        async def primitive(self, kind, **args):
            if kind == "wait": clock[0] += args["ms"] / 1000
            else: await super().primitive(kind, **args)
        async def observe(self):
            self.current_frame = encode(Image.new("RGB", (854, 480), "white")) if name == "mine" else scene(item=True)
            return self.current_frame
    ex = Executor(PixelClient(), 854, 480)
    ex.begin_step(scene())
    assert await ex.reflex(name, 4) == outcome
    assert clock[0] < 4
    assert not ex.held_buttons and not ex.held_keys


@pytest.mark.parametrize("name", ["approach_until_outline", "mine", "collect"])
async def test_reflex_timeout_empty_and_capture_error(monkeypatch, name):
    clock = [0.]
    monkeypatch.setattr("mc_observatory.holo_tools.time", type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])}))
    class Executor(ToolExecutor):
        async def primitive(self, kind, **args):
            if kind == "wait": clock[0] += args["ms"] / 1000
            else: await super().primitive(kind, **args)
        async def observe(self):
            if self.client.fail: raise OSError("capture")
            return scene()
    client = PixelClient(); client.fail = False
    ex = Executor(client, 854, 480); ex.begin_step(scene())
    assert await ex.reflex(name, .2) == "timeout"
    assert not ex.held_keys and not ex.held_buttons
    client.fail = True
    with pytest.raises(OSError, match="capture"):
        await ex.reflex(name, 1)
    assert not ex.held_keys and not ex.held_buttons


@pytest.mark.parametrize("name,signal,outcome", [
    ("approach_until_outline", "outline", "outline_pixel_cue"),
    ("mine", "broken_caption", "block_gone_pixel_cue"),
    ("collect", "hotbar_change", "hotbar_changed"),
])
async def test_shortest_reflex_allows_a_real_capture_window(monkeypatch, name, signal, outcome):
    values = iter([False, True]) if signal == "outline" else iter([0, 255]) if signal == "broken_caption" else iter([True])
    monkeypatch.setattr(f"mc_observatory.holo_tools.{signal}", lambda *args: next(values))
    class Executor(ToolExecutor):
        async def observe(self):
            await asyncio.sleep(.03)
            self.current_frame = encode(Image.new("RGB", (854, 480), "white"))
            return self.current_frame
    ex = Executor(PixelClient(), 854, 480)
    ex.begin_step(scene())
    assert await ex.reflex(name, .2) == outcome
    assert not ex.held_keys and not ex.held_buttons


async def test_request_image_hard_cap_with_many_feedback_frames(tmp_path):
    c = PixelClient()
    api = FakeAPI(c)
    agent = HoloAgent(HoloConfig(history_images=99), api)
    messages = agent.messages([(i, PNG) for i in range(12)], [PNG]*20)
    assert sum(p["type"] == "image_url" for p in messages[1]["content"]) == 4


async def test_api_error_body_is_persisted(tmp_path):
    class API:
        async def complete(self, payload):
            raise HoloAPIError([{"status": 400, "body": "too many images", "truncated": False}])
    agent = HoloAgent(HoloConfig(max_steps=1), API())
    record = [r async for r in agent.steps(PixelClient(), "observe", tmp_path)][0]
    assert record["api_errors"][0]["body"] == "too many images"
    assert "api_error" in record["failure_tags"]


async def test_http_error_redaction_and_recovered_429(monkeypatch):
    from unittest.mock import AsyncMock
    attempts = []
    class Response:
        headers = {"Retry-After": "0"}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return response()
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, *args, **kwargs):
            r = Response(); attempts.append(r)
            r.status = 429 if len(attempts) == 1 else 200
            r.content = MagicMock(readexactly=AsyncMock(side_effect=asyncio.IncompleteReadError(b"token test-secret Bearer echoed-secret", 65537)))
            return r
    monkeypatch.setattr("mc_observatory.holo.aiohttp.ClientSession", Session)
    monkeypatch.setattr("mc_observatory.holo.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(HoloAPI, "_locks", {})
    monkeypatch.setattr(HoloAPI, "_next", {})
    result = await HoloAPI("test-secret").complete({"model": "holo4-27b"})
    assert len(attempts) == 2
    assert result["_api_errors"][0]["body"] == "token [REDACTED] Bearer [REDACTED]"


@pytest.mark.parametrize("recover", [False, True])
async def test_api_twenty_second_timeout_retries_once_and_records_attempts(monkeypatch, recover):
    from unittest.mock import AsyncMock
    class API(HoloAPI):
        async def _request(self, session, payload, errors, request_timeout):
            if recover and self.last_attempts == 2:
                return {"_api_errors": errors}
            raise TimeoutError
    monkeypatch.setattr("mc_observatory.holo.asyncio.sleep", AsyncMock())
    api = API("test-key")
    if recover:
        result = await api.complete({"model": "fake"})
        assert len(result["_api_errors"]) == 1
    else:
        with pytest.raises(TimeoutError):
            await api.complete({"model": "fake"})
        assert len(api.last_errors) == 2
    assert api.last_attempts == 2
    assert api.last_errors[0]["timeout_s"] == 20


async def test_retry_allows_full_configured_timeout_after_short_attempt(monkeypatch):
    class API(HoloAPI):
        short_timeout = .01
        async def _request(self, session, payload, errors, request_timeout):
            await asyncio.sleep(.03)
            assert request_timeout == self.api_timeout == .06
            return {"_api_errors": errors}
    api = API("test-key", api_timeout=.06)
    monkeypatch.setattr(HoloAPI, "_locks", {})
    monkeypatch.setattr(HoloAPI, "_next", {})
    api._next["test"] = -100
    async def no_rate_wait(*args):
        pass
    original_sleep = asyncio.sleep
    class TestAPI(API):
        async def _request(self, session, payload, errors, request_timeout):
            if self.last_attempts == 1:
                raise TimeoutError
            await original_sleep(.03)
            assert request_timeout == .06
            return {"_api_errors": errors}
    monkeypatch.setattr("mc_observatory.holo.asyncio.sleep", no_rate_wait)
    api = TestAPI("test-key", api_timeout=.06)
    result = await api.complete({"model": "test"})
    assert api.last_attempts == 2 and len(result["_api_errors"]) == 1
    assert result["_api_errors"][0]["timeout_s"] == .01
    assert api.total_timeout > .01 + .06
