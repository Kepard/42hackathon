import io
from pathlib import Path

import pytest

from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.vision import changes, image, persistent_darkening
from test_holo import PixelClient


FIXTURES = Path(__file__).parent / "fixtures"
BOX = (.33, .46, .58, .85)


def frame(name):
    return (FIXTURES / name).read_bytes()


def altered(before, box, color):
    im = image(before)
    im.paste(color, box)
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def test_real_dark_cracks_are_not_a_strong_break_or_particle_signal():
    before = frame("dark_bark_before_460.png")
    first = frame("dark_bark_probe_460.png")
    second = frame("dark_bark_confirm_460.png")
    delta = changes(before, first, BOX)
    assert delta["center_change"] >= .002
    assert delta["center_changed_fraction"] < .01
    assert persistent_darkening(before, first, first, BOX) >= .01
    assert persistent_darkening(before, first, second, BOX) >= .01
    assert persistent_darkening(before, first, before, BOX) == 0


@pytest.mark.parametrize("mode", ["cracks", "air", "non_target", "bright_particles", "dark_particles", "stationary_particles", "break_during_confirmation"])
async def test_probe_requires_persistent_target_cracks_and_releases_on_break(monkeypatch, mode):
    before = frame("dark_bark_before_460.png")
    first = frame("dark_bark_probe_460.png")
    second = frame("dark_bark_confirm_460.png")
    broken = frame("dark_bark_broken_460.png")
    if mode == "air":
        first = second = before
    elif mode == "non_target":
        first = second = altered(before, (0, 0, 160, 160), "black")
    elif mode == "bright_particles":
        first = altered(before, (330, 285, 350, 305), "white")
        second = altered(before, (405, 320, 425, 340), "white")
    elif mode == "dark_particles":
        first = altered(before, (330, 285, 350, 305), "black")
        second = altered(before, (405, 320, 425, 340), "black")
    elif mode == "stationary_particles":
        first = second = altered(before, (330, 285, 350, 305), "black")
    elif mode == "break_during_confirmation":
        second = broken
    clock, held, released, captures = [0.], [None], [False], [0]
    monkeypatch.setattr("mc_observatory.holo_tools.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda *_: False)

    class Client(PixelClient):
        async def screenshot(self):
            if held[0] is None:
                return before
            if released[0]:
                return broken if mode in ("cracks", "break_during_confirmation") else before
            elapsed = clock[0] - held[0]
            if elapsed >= 3:
                return broken
            captures[0] += 1
            return first if captures[0] == 1 else second

        async def mouse(self, **args):
            await super().mouse(**args)
            if args.get("pressed"):
                held[0] = clock[0]
            elif "pressed" in args:
                released[0] = True

    class Executor(ToolExecutor):
        async def approach_close(self):
            self.close_checks.append(dict(box=BOX, close=True))
            return True

        async def primitive(self, kind, **args):
            if kind == "wait":
                clock[0] += args["ms"] / 1000
            else:
                await super().primitive(kind, **args)

    ex = Executor(Client(), 854, 480, jev=object())
    ex.begin_step(before)
    result = await ex.execute(dict(name="attack_until_broken", arguments=dict(max_s=4)), "skills")
    probe = result["mining_probes"][0]
    positive = mode in ("cracks", "break_during_confirmation")
    assert probe["succeeded"] is positive
    assert probe["broke_pixel_cue"] is positive
    assert result["outcome"] == ("scene_changed" if positive else "not_in_reach")
    assert not ex.held_buttons
    if mode == "break_during_confirmation":
        assert clock[0] - .35 <= .8


@pytest.mark.parametrize("invalid", [b"", b"not PNG"])
def test_persistent_darkening_rejects_invalid_images(invalid):
    from PIL import UnidentifiedImageError
    with pytest.raises(UnidentifiedImageError):
        persistent_darkening(invalid, invalid, invalid, BOX)


async def test_real_12cdc_camera_and_swing_transients_still_do_not_confirm_break(monkeypatch):
    before = frame("stone_settled_unbroken_12cdc90.png")
    transient = frame("stone_unsettled_reference_12cdc89.png")
    held, clock = [False], [0.]
    monkeypatch.setattr("mc_observatory.holo_tools.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda *_: False)

    class Client(PixelClient):
        async def screenshot(self):
            return transient if held[0] else before

        async def mouse(self, **args):
            held[0] = args.get("pressed", held[0])

    class Executor(ToolExecutor):
        async def approach_close(self):
            self.close_checks.append(dict(box=(.3, .3, .7, .7), close=True))
            return True

        async def primitive(self, kind, **args):
            if kind == "wait":
                clock[0] += args["ms"] / 1000
            else:
                await super().primitive(kind, **args)

    ex = Executor(Client(), 854, 480, jev=object())
    ex.begin_step(before)
    result = await ex.execute(dict(name="attack_until_broken", arguments=dict(max_s=4)), "skills")
    assert result["mining_probes"][0]["candidate_break"]
    assert not result["mining_probes"][0]["broke_pixel_cue"]
    assert result["outcome"] == "no_change"
    assert not result["scene_changed"]
