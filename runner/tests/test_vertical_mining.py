from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.vision import outline, outline_box
from test_holo import PixelClient
from test_pixel_skills import png


@pytest.mark.parametrize("fixture",["vertical_grass_seam_137.png","vertical_grass_seam_138.png"])
@pytest.mark.parametrize("skill",["mine_up","mine_down"])
async def test_real_floor_seam_without_outline_still_gets_full_bounded_hold(monkeypatch,fixture,skill):
    frame=(Path(__file__).parent/"fixtures"/fixture).read_bytes()
    assert not outline(frame) and outline_box(frame) is None
    monkeypatch.setattr("mc_observatory.holo_tools.asyncio.sleep",AsyncMock())
    client=PixelClient();client.screenshot=AsyncMock(return_value=frame)
    vision=type("Vision",(),{"ask":AsyncMock(side_effect=AssertionError("vertical mining must not ask Jev"))})()
    ex=ToolExecutor(client,854,480,vision);ex.begin_step(frame)
    ex.approach_close=AsyncMock(side_effect=AssertionError("vertical mining must not gate"))
    result=await ex.execute({"name":skill,"arguments":{"count":1,"break_s":.75}},"skills")
    assert result["outcome"]==["no_change"] and len(result["vertical_mining"])==2
    assert [a["ms"] for a in ex.actions if a["action"]=="wait"]==[1100,200,1100,200]
    assert sum(a.get("button")==0 and a.get("pressed") is True for a in ex.actions)==2
    assert ex.actions[:2]==[{"action":"mouse","dx":0,"dy":1200},{"action":"mouse","dx":0,"dy":-1200 if skill=="mine_up" else 0}]
    assert not ex.held_keys and not ex.held_buttons
    vision.ask.assert_not_awaited();ex.approach_close.assert_not_awaited()


async def test_vertical_pickup_stops_retry_and_attempt_metadata_is_action_local(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.asyncio.sleep",AsyncMock())
    before,after=png("black",(854,480)),png("white",(854,480))
    client=PixelClient();client.screenshot=AsyncMock(side_effect=[before,after,after,after,after,after,after])
    ex=ToolExecutor(client,854,480);ex.begin_step(before)
    first=await ex.execute({"name":"mine_down","arguments":{"count":1,"break_s":1.15}},"skills")
    assert len(first["vertical_mining"])==1 and first["hotbar_changed"]
    second=await ex.execute({"name":"mine_up","arguments":{"count":1}},"skills")
    assert len(second["vertical_mining"])==2 and not second["hotbar_changed"]


async def test_vertical_hold_releases_attack_on_error(monkeypatch):
    client=PixelClient();ex=ToolExecutor(client,854,480);ex.begin_step(png("black"))
    monkeypatch.setattr("mc_observatory.holo_tools.asyncio.sleep",AsyncMock(side_effect=RuntimeError("wait failed")))
    with pytest.raises(RuntimeError,match="wait failed"):
        await ex.execute({"name":"mine_down","arguments":{"count":1}},"skills")
    assert not ex.held_buttons
    assert ("mouse",{"button":0,"pressed":False}) in client.events
