import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mc_observatory.holo_tools import InvalidToolArgs, ToolExecutor, observation_prefix, parse_calls, tool_schemas
from mc_observatory.vision import crafting_controls, crafting_slot, filtered_recipe_icons, stack_count, container_gui_visible
from test_holo import PNG, PixelClient, call


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name): return (FIXTURES/name).read_bytes()


def encode(im):
    out=io.BytesIO();im.save(out,format="PNG");return out.getvalue()


def altered(png,mode):
    im=Image.open(io.BytesIO(png)).convert("RGB");draw=ImageDraw.Draw(im)
    if mode=="missing": draw.rectangle((130,136,177,183),fill=(55,55,55))
    elif mode=="multiple": im.paste(im.crop((130,136,178,184)),(180,136))
    elif mode=="ghost_recipe": draw.rectangle((138,144,169,175),fill=(174,104,104))
    elif mode=="ghost_output": draw.rectangle((712,130,743,161),fill=(174,104,104))
    elif mode=="empty_output": draw.rectangle((712,130,743,161),fill=(139,139,139))
    return encode(im)


@pytest.mark.parametrize("factor",[.5,1,1.5,3])
@pytest.mark.parametrize("recipe,yield_count,remaining",[("table",1,12),("stick",4,10)])
def test_real_inventory_recipe_output_and_hotbar_pixels(factor,recipe,yield_count,remaining):
    frames=[]
    for phase in ("ready","output","taken"):
        im=Image.open(io.BytesIO(fixture(f"craft_{recipe}_{phase}_933.png")))
        frames.append(encode(im.resize((round(im.width*factor),round(im.height*factor)),Image.Resampling.NEAREST)))
    ready,output,taken=frames;layout=crafting_controls(ready);s=layout["scale"]
    assert layout["kind"]=="inventory" and layout["book_open"]
    assert len(filtered_recipe_icons(ready))==1
    assert container_gui_visible(ready)
    assert not crafting_slot(ready,layout["output"],s)["occupied"]
    assert crafting_slot(output,layout["output"],s)=={"occupied":True,"ghost":False}
    assert stack_count(output,layout["output"],s)==yield_count
    assert not crafting_slot(taken,layout["output"],s)["occupied"]
    assert stack_count(taken,layout["hotbar"][0],s)==remaining


def test_real_table_layout_and_non_gui():
    for name in ("craft_pick_table_ready_442.png","craft_pick_table_output_442.png"):
        p=fixture(name);layout=crafting_controls(p)
        assert layout["kind"]=="table"
        assert len(layout["inputs"])==9 and layout["output"]==(668,160)
        assert container_gui_visible(p)
    closed=fixture("inventory_closed_book_c226_36.png")
    assert crafting_controls(closed)["book_open"] is False
    for p in (PNG,fixture("mining_seam_progress_442_15.png")):
        assert crafting_controls(p) is None
        assert not container_gui_visible(p)
    for p in (b"",b"bad"):
        with pytest.raises(OSError): crafting_controls(p)


def test_focused_recipe_border_is_not_an_extra_recipe():
    p=fixture("craft_pick_focused_ready_933.png")
    assert len(filtered_recipe_icons(p))==1


class CraftClient(PixelClient):
    def __init__(self,recipe="table",mode=None):
        super().__init__();self.recipe=recipe;self.mode=mode;self.cursor=None;self.keys=set();self.takes=0
        self.ready=fixture(f"craft_{recipe}_ready_933.png")
        self.loaded=fixture(f"craft_{recipe}_output_933.png")
        self.taken=fixture(f"craft_{recipe}_taken_933.png")
        self.frame=altered(self.ready,mode) if mode in ("missing","multiple","ghost_recipe") else self.ready
        layout=crafting_controls(self.ready);self.output=layout["output"];self.search=(239,114)
        self.result=filtered_recipe_icons(self.ready)[0]["center"]

    async def screenshot(self):
        self.events.append("screenshot")
        if self.takes and self.cursor==self.output:
            im=Image.open(io.BytesIO(self.frame));ImageDraw.Draw(im).rectangle((712,130,743,161),fill=(197,197,197));return encode(im)
        return self.frame

    async def key(self,code,**args):
        await super().key(code,**args)
        (self.keys.add if args["pressed"] else self.keys.discard)(code)

    async def type_text(self,text):
        await super().type_text(text)
        if self.mode=="type_error": raise RuntimeError("typing rejected")

    async def mouse(self,**args):
        await super().mouse(**args)
        if "x" in args: self.cursor=(args["x"],args["y"])
        if not args.get("pressed"): return
        if self.cursor==self.result:
            self.frame=altered(self.loaded,self.mode)
        elif self.cursor==self.output and 340 in self.keys:
            if self.mode=="take_error": raise RuntimeError("mouse rejected")
            if self.mode=="full": return
            self.takes+=1;self.frame=self.taken


@pytest.mark.parametrize("recipe,count,expected",[("table",1,1),("table",2,2),("stick",1,4),("stick",4,4),("stick",5,8)])
async def test_atomic_single_batch_transfers_and_minimum_count_rounding(recipe,count,expected):
    client=CraftClient(recipe);ex=ToolExecutor(client,854,480);ex.begin_step(client.frame)
    r=await ex.execute({"name":"craft","arguments":{"item":"crafting table" if recipe=="table" else "stick","count":count}},"skills")
    assert r["outcome"]=="crafted"
    assert r["crafting"]["items_taken"]==expected
    assert r["crafting"]["hotbar_slots"]==[{"slot":9 if recipe=="table" else 8,"count":1 if recipe=="table" else 4}]
    assert not ex.held_keys and not ex.held_buttons
    assert client.cursor != client.output


@pytest.mark.parametrize("mode,outcome",[
    ("missing","recipe_result_missing_or_ambiguous"),("multiple","recipe_result_missing_or_ambiguous"),
    ("ghost_recipe","recipe_result_empty_or_ghost"),("ghost_output","craft_output_empty_or_ghost"),
    ("empty_output","craft_output_empty_or_ghost"),("full","craft_output_not_taken"),
])
async def test_ambiguous_missing_ghost_empty_or_full_inventory_fails_closed(mode,outcome):
    c=CraftClient(mode=mode);ex=ToolExecutor(c,854,480);ex.begin_step(c.frame)
    r=await ex.execute({"name":"craft","arguments":{"item":"crafting table","count":1}},"skills")
    assert r["outcome"]==outcome and r["crafting"]["items_taken"]==0
    assert c.takes==0 and not ex.held_keys and not ex.held_buttons
    assert "do not repeat" in r["next_action"]


@pytest.mark.parametrize("mode",["type_error","take_error"])
async def test_errors_release_owned_modifiers_and_mouse(mode):
    c=CraftClient(mode=mode);ex=ToolExecutor(c,854,480);ex.begin_step(c.frame)
    with pytest.raises(RuntimeError,match="rejected"):
        await ex.execute({"name":"craft","arguments":{"item":"crafting table","count":1}},"skills")
    assert not ex.held_keys and not ex.held_buttons


async def test_open_gui_empty_grid_and_released_inputs_are_required():
    c=CraftClient();ex=ToolExecutor(c,854,480);ex.begin_step(PNG)
    r=await ex.execute({"name":"craft","arguments":{"item":"crafting table","count":1}},"skills")
    assert r["outcome"]=="open_inventory_or_table_first"
    ex.begin_step(c.loaded)
    c.frame=c.loaded
    r=await ex.execute({"name":"craft","arguments":{"item":"crafting table","count":1}},"skills")
    assert r["outcome"]=="craft_grid_not_empty" and not c.takes
    await ex.key("w",True)
    r=await ex.execute({"name":"craft","arguments":{"item":"crafting table","count":1}},"skills")
    assert r["outcome"]=="release_inputs_before_crafting" and ex.held_keys=={"w"}
    await ex.release()


def test_craft_schema_and_observation_barrier():
    assert "craft" in {t["function"]["name"] for t in tool_schemas("skills","jev")}
    assert "craft" not in {t["function"]["name"] for t in tool_schemas("raw")}
    for value in (0,65,True,"4"):
        with pytest.raises(InvalidToolArgs): parse_calls({"tool_calls":[call("craft",item="stick",count=value)]},"skills")
    first=call("craft",item="stick",count=4);second=call("look",yaw_deg=0,pitch_deg=10)
    assert observation_prefix([first,second],"jev")==([first],[second])
