import io
from types import SimpleNamespace

import pytest
from PIL import Image

from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.vision import close_face, movement_danger, outline_box
from test_holo import PixelClient


def frame(color="gray", size=(854, 480)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def setup(monkeypatch, *, near=False, blocked=False, size=(854, 480), danger=False, error=False):
    clock = [0.]
    async def sleep(seconds): clock[0] += max(.001, seconds)
    monkeypatch.setattr("mc_observatory.holo_tools.time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr("mc_observatory.holo_tools.asyncio.sleep", sleep)
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda *args: True)
    class Client(PixelClient):
        walks = 0
        attacking = False
        async def key(self, code, **kwargs):
            if code == 87 and kwargs.get("pressed"): self.walks += 1
            await super().key(code, **kwargs)
        async def mouse(self, **kwargs):
            if kwargs.get("button") == 0: self.attacking = kwargs.get("pressed", False)
            await super().mouse(**kwargs)
        async def screenshot(self):
            return frame("orange" if danger else "white" if self.attacking else "green" if self.walks and not blocked else "gray", size)
    client = Client()
    class Vision:
        calls = []
        async def ask(self, png, target):
            assert png.startswith(b"\x89PNG") and isinstance(target, str)
            if error: raise RuntimeError("vision unavailable")
            return {"box": [.35, .3, .65, .7] if near or (client.walks and not blocked) else [.45, .4, .55, .6], "noul": .99}
    ex = ToolExecutor(client, *size, Vision())
    ex.begin_step(frame(size=size))
    return client, ex


@pytest.mark.parametrize("size", [(854,480),(2560,1440)])
async def test_far_face_walks_first_then_mines(monkeypatch, size):
    client, ex = setup(monkeypatch, size=size)
    result = await ex.execute({"name":"attack_until_broken","arguments":{"max_s":.35}}, "skills")
    assert client.walks == 1
    assert result["close_checks"][-1]["close"]
    walk = client.events.index(("key",87,{"pressed":True}))
    attack = client.events.index(("mouse",{"button":0,"pressed":True}))
    assert walk < attack and not ex.held_keys and not ex.held_buttons


async def test_close_face_mines_immediately(monkeypatch):
    client, ex = setup(monkeypatch, near=True)
    result = await ex.execute({"name":"attack_until_broken","arguments":{"max_s":.35}}, "skills")
    assert client.walks == 0 and result["close_checks"][0]["close"]
    assert ("mouse",{"button":0,"pressed":True}) in client.events


@pytest.mark.parametrize("options", [{"blocked":True},{"danger":True},{"error":True}])
async def test_cannot_close_returns_seek_hint_without_attack_or_probe(monkeypatch, options):
    client, ex = setup(monkeypatch, **options)
    result = await ex.execute({"name":"attack_until_broken","arguments":{"max_s":.35}}, "skills")
    assert result["outcome"] == "not_close"
    assert result["next_action"] == "call seek(<target>) first"
    assert result["mining_probes"] == [{"used":False,"succeeded":None,"skipped":"not_close"}]
    assert ("mouse",{"button":0,"pressed":True}) not in client.events
    assert not ex.held_keys and not ex.held_buttons


@pytest.mark.parametrize("tool", ["mine_up","mine_down"])
async def test_vertical_target_bypasses_horizontal_close_gate(monkeypatch, tool):
    client, ex = setup(monkeypatch)
    result = await ex.execute({"name":tool,"arguments":{"count":1}}, "skills")
    assert result["outcome"] == ["no_change"] and client.walks == 0
    assert result.get("close_checks",[]) == []
    assert ("mouse",{"button":0,"pressed":True}) in client.events


def test_close_size_and_danger_are_pixel_scaled():
    assert not close_face([.45,.4,.55,.6],2560,1440)
    assert close_face([.35,.3,.65,.7],2560,1440)
    assert not close_face(None,854,480)
    assert movement_danger(frame("orange")) == "lava_pixel_cue"
    assert movement_danger(frame("gray")) is None


def test_native_outline_retains_close_geometry_without_jev():
    from PIL import ImageDraw
    im = Image.new("RGB",(854,480),"gray")
    ImageDraw.Draw(im).rectangle((330,150,524,330),outline="black",width=2)
    out=io.BytesIO();im.save(out,format="PNG")
    assert close_face(outline_box(out.getvalue()),854,480)


async def test_entity_attack_never_requires_a_block_face(monkeypatch):
    from unittest.mock import AsyncMock
    client,ex=setup(monkeypatch)
    ex.approach_close=AsyncMock(side_effect=AssertionError("not a block action"))
    await ex.execute({"name":"attack","arguments":{"seconds":.1}},"skills")
    assert ("mouse",{"button":0,"pressed":True}) in client.events
    assert client.walks==0


async def test_face_filling_centre_crop_counts_close_without_outline_edges_in_crop(monkeypatch):
    from PIL import ImageDraw
    from unittest.mock import AsyncMock
    from mc_observatory.vision import large_face_box
    im=Image.new("RGB",(854,480),"gray")
    ImageDraw.Draw(im).rectangle((207,80,647,400),outline="black",width=2)
    out=io.BytesIO();im.save(out,format="PNG");png=out.getvalue()
    assert outline_box(png) is None
    assert close_face(large_face_box(png),854,480)
    assert large_face_box(frame()) is None
    client,ex=setup(monkeypatch);ex.jev=None
    client.screenshot=AsyncMock(return_value=png)
    assert await ex.approach_close()
    assert client.walks==0


@pytest.mark.parametrize("error",[False,True])
async def test_approach_only_releases_w_it_owns(monkeypatch,error):
    client,ex=setup(monkeypatch,near=True,error=error)
    await ex.key("w",True)
    assert await ex.approach_close() is (not error)
    assert ex.held_keys=={"w"}
    assert ("key",87,{"pressed":False}) not in client.events
    await ex.release()


@pytest.mark.parametrize("tool",["mine_up","mine_down"])
async def test_vertical_mining_ignores_saved_horizontal_target(monkeypatch,tool):
    client,ex=setup(monkeypatch,near=True)
    ex.seek_target="oak log trunk"
    targets=[]
    class StoneVision:
        async def ask(self,png,target):
            targets.append(target)
            return {"box":[.35,.3,.65,.7],"noul":0 if target=="oak log trunk" else .99}
    ex.jev=StoneVision()
    result=await ex.execute({"name":tool,"arguments":{"count":1}},"skills")
    assert result["outcome"] != "not_close"
    assert targets==[]
    assert ("mouse",{"button":0,"pressed":True}) in client.events
    assert client.walks==0


@pytest.mark.parametrize("width",[180,200,239])
async def test_tall_close_faces_use_same_width_threshold(monkeypatch,width):
    from PIL import ImageDraw
    from unittest.mock import AsyncMock
    from mc_observatory.vision import large_face_box
    im=Image.new("RGB",(854,480),"gray")
    ImageDraw.Draw(im).rectangle((427-width//2,40,427+width//2,440),outline="black",width=2)
    out=io.BytesIO();im.save(out,format="PNG");png=out.getvalue()
    assert outline_box(png) is None
    assert close_face(large_face_box(png),854,480)
    client,ex=setup(monkeypatch);ex.jev=None
    client.screenshot=AsyncMock(return_value=png)
    assert await ex.approach_close()
