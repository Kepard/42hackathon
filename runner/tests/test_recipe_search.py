import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mc_observatory.holo_tools import ToolExecutor, parse_calls, tool_schemas
from mc_observatory.vision import recipe_book_controls
from test_holo import PNG, PixelClient, call


FIXTURES = Path(__file__).parent / "fixtures"
RED = (FIXTURES / "recipe_unsearched_oak_wood.png").read_bytes()
GREEN = (FIXTURES / "recipe_craftable_search.png").read_bytes()


def parsed(name, **args):
    return parse_calls({"tool_calls":[call(name,**args)]},"skills")[0]


@pytest.mark.parametrize("factor", [.5,1,1.5,3])
def test_real_recipe_controls_at_multiple_gui_scales(factor):
    for source, craftable, query in ((RED,False,False),(GREEN,True,True)):
        im = Image.open(io.BytesIO(source))
        out = io.BytesIO()
        im.resize((round(im.width*factor),round(im.height*factor)),Image.Resampling.NEAREST).save(out,format="PNG")
        controls = recipe_book_controls(out.getvalue())
        assert controls is not None
        assert controls["craftable_only"] is craftable
        assert controls["query_present"] is query


def test_missing_empty_invalid_and_nonrecipe_controls():
    assert recipe_book_controls(PNG) is None
    assert recipe_book_controls((FIXTURES / "inventory_closed_book_c226_36.png").read_bytes()) is None
    with pytest.raises(OSError): recipe_book_controls(b"")
    with pytest.raises(OSError): recipe_book_controls(b"invalid")
    assert "search_recipe" not in {t["function"]["name"] for t in tool_schemas("raw")}


class RecipeClient(PixelClient):
    def __init__(self, frame=RED, fail=False, toggle=True):
        super().__init__()
        self.frame, self.cursor, self.fail, self.toggle = frame, None, fail, toggle

    async def screenshot(self):
        self.events.append("screenshot")
        return self.frame

    async def mouse(self, **args):
        await super().mouse(**args)
        if "x" in args: self.cursor = (args["x"],args["y"])
        if args.get("pressed") and self.cursor == recipe_book_controls(RED)["filter"] and self.toggle:
            self.frame = GREEN

    async def type_text(self, text):
        await super().type_text(text)
        if self.fail: raise RuntimeError("typing rejected")
        im = Image.open(io.BytesIO(self.frame)).convert("RGB")
        draw = ImageDraw.Draw(im)
        draw.rectangle((160,102,317,125),fill="black")
        if text.strip(): draw.rectangle((166,108,198,120),fill="white")
        out = io.BytesIO()
        im.save(out,format="PNG")
        self.frame = out.getvalue()


@pytest.mark.parametrize("frame",[RED,GREEN])
async def test_atomic_search_sets_filter_and_types_with_owned_modifiers(frame):
    client = RecipeClient(frame)
    ex = ToolExecutor(client,854,480)
    ex.begin_step(frame)
    result = await ex.execute(parsed("search_recipe",query="oak planks"),"skills")
    assert result["outcome"] == "recipe_search_ready"
    assert ("type","oak planks") in client.events
    assert ("key",341,{"pressed":True}) in client.events
    assert ("key",341,{"pressed":False}) in client.events
    assert ("key",65,{"pressed":True}) in client.events
    assert not ex.held_keys and not ex.held_buttons
    moves = [e[1] for e in client.events if isinstance(e,tuple) and e[0]=="mouse" and "x" in e[1]]
    assert len(moves) == (2 if frame==RED else 1)


@pytest.mark.parametrize("name",["click_at","shift_click_at"])
@pytest.mark.parametrize("tier",["skills","raw"])
async def test_unsearched_ghost_recipe_click_is_blocked_only_in_skills(name,tier):
    client = RecipeClient()
    ex = ToolExecutor(client,854,480)
    ex.begin_step(RED)
    value = await ex.execute({"name":name,"arguments":{"x":175,"y":335}},tier)
    dispatched = [e for e in client.events if isinstance(e,tuple) and e[0]=="mouse"]
    if tier=="skills":
        assert not dispatched
        assert value["outcome"] == "search_recipe_first"
        assert "search_recipe" in value["next_action"]
    else: assert dispatched


async def test_search_error_and_unconfirmed_filter_release_inputs():
    for failure,toggle in ((True,True),(False,False)):
        client = RecipeClient(fail=failure,toggle=toggle)
        ex = ToolExecutor(client,854,480)
        ex.begin_step(RED)
        if failure:
            with pytest.raises(RuntimeError,match="typing rejected"):
                await ex.execute(parsed("search_recipe",query="stick"),"skills")
        else:
            result = await ex.execute(parsed("search_recipe",query="stick"),"skills")
            assert result["outcome"] == "recipe_filter_unconfirmed"
            assert not any(isinstance(e,tuple) and e[0]=="type" for e in client.events)
        assert not ex.held_keys and not ex.held_buttons


async def test_missing_book_and_nonrecipe_clicks_remain_available():
    client = RecipeClient(PNG)
    ex = ToolExecutor(client,640,480)
    ex.begin_step(PNG)
    result = await ex.execute(parsed("search_recipe",query="stick"),"skills")
    assert result["outcome"].startswith("recipe_book_not_visible")
    assert not any(isinstance(e,tuple) for e in client.events)
    await ex.execute(parsed("click_at",x=500,y=500),"skills")
    assert any(isinstance(e,tuple) and e[0]=="mouse" for e in client.events)


@pytest.mark.parametrize("query_present,craftable",[(False,True),(True,False),(True,True)])
async def test_recipe_click_requires_both_query_pixels_and_green_filter(query_present,craftable):
    client = RecipeClient(GREEN if craftable else RED)
    await client.type_text("stick" if query_present else "")
    client.events.clear()
    ex = ToolExecutor(client,854,480)
    ex.begin_step(client.frame)
    result = await ex.execute(parsed("click_at",x=175,y=335),"skills")
    dispatched = any(isinstance(e,tuple) and e[0]=="mouse" for e in client.events)
    assert dispatched is (query_present and craftable)
    if not dispatched: assert result["outcome"] == "search_recipe_first"


async def test_empty_search_is_unconfirmed_not_ready():
    client = RecipeClient(GREEN)
    ex = ToolExecutor(client,854,480)
    ex.begin_step(GREEN)
    result = await ex.execute(parsed("search_recipe",query=" "),"skills")
    assert result["outcome"] == "recipe_search_unconfirmed"


def test_empty_search_caret_is_not_a_query():
    im = Image.open(io.BytesIO(RED)).convert("RGB")
    ImageDraw.Draw(im).rectangle((164,106,165,121),fill="white")
    out = io.BytesIO()
    im.save(out,format="PNG")
    assert recipe_book_controls(out.getvalue())["query_present"] is False
