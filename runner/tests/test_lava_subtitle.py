import io
from pathlib import Path

import pytest
from PIL import Image, ImageEnhance

from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.vision import lava_sound_nearby, movement_danger
from test_holo import PixelClient


FIXTURES = Path(__file__).parent / "fixtures"
LAVA = (FIXTURES / "lava_pops_before_retreat_442_197.png").read_bytes()


@pytest.mark.parametrize("factor",[.5,1,1.5,3])
@pytest.mark.parametrize("brightness",[1,.67])
def test_real_subtitle_at_different_scales_and_fade_levels(factor,brightness):
    im = ImageEnhance.Brightness(Image.open(io.BytesIO(LAVA))).enhance(brightness)
    im = im.resize((round(im.width*factor),round(im.height*factor)),Image.Resampling.NEAREST)
    out = io.BytesIO();im.save(out,format="PNG")
    assert lava_sound_nearby(out.getvalue())
    assert movement_danger(LAVA) is None


@pytest.mark.parametrize("name",["recipe_craftable_search.png","mining_seam_progress_442_15.png","outline_bark_oblique.png"])
def test_other_gui_and_sound_text_does_not_match(name):
    assert not lava_sound_nearby((FIXTURES/name).read_bytes())


def test_subtitle_area_only_and_invalid_images():
    source = Image.open(io.BytesIO(LAVA))
    caption = source.crop((728,382,830,398))
    for position,expected in (((710,340),True),((20,30),False)):
        im = Image.new("RGB",source.size,"black");im.paste(caption,position)
        out = io.BytesIO();im.save(out,format="PNG")
        assert lava_sound_nearby(out.getvalue()) is expected
    for color in ("black","white"):
        out = io.BytesIO();Image.new("RGB",source.size,color).save(out,format="PNG")
        assert not lava_sound_nearby(out.getvalue())
    with pytest.raises(OSError): lava_sound_nearby(b"")
    with pytest.raises(OSError): lava_sound_nearby(b"invalid")


class Client(PixelClient):
    async def screenshot(self): return LAVA


@pytest.mark.parametrize("call",[
    {"name":"reposition","arguments":{"direction":"back","seconds":0}},
    {"name":"move","arguments":{"direction":"left","seconds":0}},
    {"name":"move","arguments":{"direction":"right","seconds":0}},
    {"name":"key_down","arguments":{"key":"s","ms":0}},
    {"name":"key_press","arguments":{"key":"a","ms":0}},
    {"name":"key_down","arguments":{"key":"d","ms":0}},
])
async def test_blind_skills_movement_refuses_real_lava_sound_pixels(call):
    client = Client();ex = ToolExecutor(client,854,480);ex.begin_step(LAVA)
    result = await ex.execute(call,"skills")
    assert result["outcome"] == "lava_sound_nearby"
    assert "lava sound nearby" in result["next_action"]
    assert not any(isinstance(e,tuple) for e in client.events)
    assert not ex.held_keys


@pytest.mark.parametrize("tier,name,args",[
    ("raw","key_down",{"key":"s"}),
    ("skills","key_down",{"key":"w","ms":0}),
    ("skills","key_up",{"key":"s"}),
    ("skills","key_press",{"key":"a","ctrl":True,"ms":0}),
])
async def test_raw_forward_release_and_select_all_remain_available(tier,name,args):
    client = Client();ex = ToolExecutor(client,854,480);ex.begin_step(LAVA)
    if name == "key_up": await ex.key(args["key"],True)
    await ex.execute({"name":name,"arguments":args},tier)
    await ex.finish_turn()
    assert any(isinstance(e,tuple) and e[0]=="key" for e in client.events)
