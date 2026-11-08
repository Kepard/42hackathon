from pathlib import Path

import pytest

from mc_observatory.holo import HoloAgent, HoloConfig
from test_holo import FakeAPI, PixelClient, PNG, call, response


@pytest.mark.parametrize("model",["holo4-27b","holo4-35b-a3b"])
@pytest.mark.parametrize("frame",["craft_table_ready_933.png","inventory_closed_book_c226_36.png",None])
async def test_auto_low_effort_is_per_request_and_pixel_gui_only(tmp_path,model,frame):
    class Client(PixelClient):
        async def screenshot(self):
            return (Path(__file__).parent/"fixtures"/frame).read_bytes() if frame else PNG
    c=Client();api=FakeAPI(c,response([call("wait",seconds=0)]))
    r=[r async for r in HoloAgent(HoloConfig(model=model,max_steps=1,tier="skills",timing="realtime"),api).steps(c,"Observe pixels",tmp_path)][0]
    assert (api.payloads[0].get("reasoning_effort")=="low") is (model=="holo4-27b" and frame is not None)
    assert r["request_reasoning_effort"]==api.payloads[0].get("reasoning_effort")
    assert api.payloads[0]["max_tokens"]==4096
