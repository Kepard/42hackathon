import pytest

from mc_observatory.holo_tools import ToolExecutor
from test_holo import PixelClient, PNG


async def test_skill_timings_are_per_call_and_include_capture_and_analysis(monkeypatch):
    clock = [0.]
    monkeypatch.setattr("mc_observatory.holo_tools.perf_counter", lambda: clock[0])

    class Client(PixelClient):
        async def screenshot(self):
            clock[0] += .003
            return PNG

    def delta(*args):
        clock[0] += .005
        return dict(frame_change=0, center_change=0, center_changed_fraction=0)

    monkeypatch.setattr("mc_observatory.holo_tools.changes", delta)
    ex = ToolExecutor(Client(), 640, 480)
    ex.begin_step(PNG)
    for _ in range(2):
        result = await ex.execute({"name": "wait", "arguments": {"seconds": 0}}, "skills")
        assert result["timings"] == pytest.approx(dict(screenshot_ms=3, pixel_analysis_ms=5, screenshot_count=1))


async def test_failed_capture_and_pixel_analysis_are_timed(monkeypatch):
    clock = [0.]
    monkeypatch.setattr("mc_observatory.holo_tools.perf_counter", lambda: clock[0])

    class Client(PixelClient):
        async def screenshot(self):
            clock[0] += .003
            raise RuntimeError("capture failed")

    ex = ToolExecutor(Client(), 640, 480)
    with pytest.raises(RuntimeError, match="capture failed"):
        await ex.observe()
    assert ex.screenshot_ms == pytest.approx(3)
    assert ex.screenshot_count == 1
    assert ex.frames == []

    def invalid():
        clock[0] += .005
        raise ValueError("invalid pixels")

    with pytest.raises(ValueError, match="invalid pixels"):
        ex.pixels(invalid)
    assert ex.pixel_analysis_ms == pytest.approx(5)
