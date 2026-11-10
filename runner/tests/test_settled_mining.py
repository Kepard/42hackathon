from pathlib import Path

import pytest

from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.vision import changes
from test_holo import PixelClient


FIXTURES = Path(__file__).parent/"fixtures"


def frame(name): return (FIXTURES/name).read_bytes()


def test_real_bobbing_reference_is_not_a_stable_break_baseline():
    unsettled=frame("stone_unsettled_reference_12cdc89.png")
    transient=frame("stone_transient_hold_12cdc89.png")
    settled=frame("stone_settled_unbroken_12cdc90.png")
    assert changes(unsettled,transient)["center_changed_fraction"]>.7
    assert changes(unsettled,settled)["center_change"]>.08
    assert changes(settled,settled)["center_change"]==0
    before=frame("stone_still_before_12cdc.png")
    cracks=frame("stone_still_cracks_12cdc.png")
    broken=frame("stone_still_broken_12cdc.png")
    assert changes(before,cracks)["center_changed_fraction"]<.7
    assert changes(before,broken)["center_changed_fraction"]>=.7


@pytest.mark.parametrize("walked",[False,True])
async def test_settled_reference_and_post_release_core_reject_transients(monkeypatch,walked):
    clock=[0.];held=[None];released=[False]
    baseline=frame("stone_still_before_12cdc.png") if walked else frame("stone_settled_unbroken_12cdc90.png")
    transient=frame("stone_unsettled_reference_12cdc89.png")
    cracks=frame("stone_still_cracks_12cdc.png")
    broken=frame("stone_still_broken_12cdc.png")
    monkeypatch.setattr("mc_observatory.holo_tools.time.monotonic",lambda:clock[0])
    monkeypatch.setattr("mc_observatory.holo_tools.outline",lambda _:False)
    class Client(PixelClient):
        async def screenshot(self):
            if held[0] is None: return transient if walked and clock[0]<.35 else baseline
            if released[0]: return broken if walked else baseline
            if not walked: return transient
            return broken if clock[0]-held[0]>=1.2 else cracks
        async def mouse(self,**args):
            await super().mouse(**args)
            if args.get("pressed"): held[0]=clock[0]
            elif "pressed" in args: released[0]=True
    class Executor(ToolExecutor):
        async def primitive(self,kind,**args):
            if kind=="wait": clock[0]+=args["ms"]/1000
            else: await super().primitive(kind,**args)
        async def approach_close(self):
            if walked:
                await self.key("w",True);await self.primitive("wait",ms=200);await self.key("w",False)
            return True
    ex=Executor(Client(),854,480,jev=object());ex.begin_step(baseline)
    result=await ex.execute({"name":"attack_until_broken","arguments":{"max_s":2.5}},"skills")
    probe=result["mining_probes"][0]
    assert probe["reference_settle_ms"]==(350 if walked else 0)
    assert probe["candidate_break"] is True
    assert probe["broke_pixel_cue"] is walked
    assert result["outcome"]==("scene_changed" if walked else "no_change")
    if not walked: assert "no break confirmed" in result["next_action"]
    assert not ex.held_buttons
