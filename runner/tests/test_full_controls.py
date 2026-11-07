import pytest

from mc_observatory.holo_tools import ToolExecutor, parse_calls, tool_schemas
from mc_observatory.session import keycode
from test_holo import PixelClient, call


@pytest.mark.parametrize("tier,reflex", [("raw","none"),("skills","none"),("skills","jev")])
def test_every_vanilla_binding_and_keyboard_modifier_is_exposed(tier, reflex):
    names = {t["function"]["name"] for t in tool_schemas(tier,reflex)}
    assert {"key_down","key_up","key_press","mouse_down","mouse_up","mouse_click","mouse_move_to","click_at","scroll"} <= names
    keys = list("abcdefghijklmnopqrstuvwxyz0123456789") + ["space","shift","ctrl","alt","right_shift","right_ctrl","tab","/","esc","enter","backspace","home","end","delete","left","right","kp_enter"] + [f"f{i}" for i in range(1,26)]
    for key in keys:
        parse_calls({"tool_calls":[call("key_press",key=key,ms=100)]},tier,reflex)
        assert isinstance(keycode(key),int)


async def test_modifiers_middle_click_scroll_double_click_and_drag_are_pixel_only():
    client = PixelClient()
    ex = ToolExecutor(client,854,480)
    async def run(name, **arguments):
        await ex.execute_inputs({"name":name,"arguments":arguments},"skills")
    await run("key_down",key="ctrl")
    await run("key_press",key="q",ms=0)
    assert "ctrl" in ex.held_keys
    await run("key_up",key="ctrl")
    await run("key_down",key="f3")
    await run("key_press",key="g",ms=0)
    await run("key_up",key="f3")
    await run("mouse_down",button="right")
    await run("mouse_move_to",x=500,y=500)
    await run("mouse_up",button="right")
    await run("click_at",x=200,y=200,button="middle",count=2)
    await run("scroll",vertical=-1)
    await run("key_down",key="shift")
    await run("shift_click_at",x=200,y=200)
    assert "shift" in ex.held_keys
    await run("key_up",key="shift")
    assert not ex.held_keys and not ex.held_buttons
    assert ("mouse",{"scroll_y":-1,"scroll_x":0}) in client.events
    assert sum(e==("mouse",{"button":2,"pressed":True}) for e in client.events)==2


async def test_alias_holds_release_only_after_last_name_and_taps_preserve_holds():
    client = PixelClient()
    ex = ToolExecutor(client,640,480)
    await ex.key("ctrl",True)
    await ex.key("left_ctrl",True)
    await ex.key("ctrl",False)
    assert ex.held_keys == {"left_ctrl"}
    assert client.events == [("key",341,{"pressed":True})]
    await ex.tap("left_ctrl",0)
    assert ex.held_keys == {"left_ctrl"}
    await ex.key("left_ctrl",False)
    assert not ex.held_keys
    assert client.events == [("key",341,{"pressed":True}),("key",341,{"pressed":False})]


async def test_freeze_restore_physically_represses_retained_aliased_keys():
    class Client(PixelClient):
        active = set()
        async def key(self, code, **fields):
            (self.active.add if fields["pressed"] else self.active.discard)(code)
            await super().key(code, **fields)
    client=Client();ex=ToolExecutor(client,640,480)
    for key in ("w","ctrl","left_ctrl"): await ex.key(key,True)
    await ex.release(retain=True)
    assert not client.active and ex.held_keys=={"w","ctrl","left_ctrl"}
    before=len(client.events)
    await ex.restore()
    assert client.active=={87,341}
    assert len(client.events)-before==2
    await ex.key("ctrl",False)
    assert client.active=={87,341}
    await ex.key("left_ctrl",False)
    assert client.active=={87}
    await ex.release()
    assert not client.active and not ex.held_keys
