"""Pixel/input-only tool vocabulary. No state API is exposed here."""

import asyncio
import json
import math
import time

from .session import keycode
from .vision import changes


class InvalidToolArgs(ValueError):
    pass


def number(low, high, description=""):
    return {"type": "number", "minimum": low, "maximum": high, "description": description}


def choice(*values):
    return {"type": "string", "enum": list(values)}


XY = {"x": number(0, 1000), "y": number(0, 1000)}
BUTTON = choice("left", "right")
KEY = choice(*list("wasd") + ["space", "shift", "ctrl", "e", "esc", "enter", "t", "q", "f", "tab", "f3"] + list("123456789"))
RAW = {
    "key_down": ("Hold a Minecraft key until key_up. Use wait to allow ticks.", {"key": KEY}),
    "key_up": ("Release a held key.", {"key": KEY}),
    "key_press": ("Press and release a key for ms milliseconds.", {"key": KEY, "ms": number(0, 10000)}),
    "mouse_move": ("Relative look units: 0.15 degrees/unit; positive dx right, dy down.", {"dx": number(-2400, 2400), "dy": number(-1200, 1200)}),
    "mouse_down": ("Hold mouse button; left mines/attacks, right uses.", {"button": BUTTON}),
    "mouse_up": ("Release mouse button.", {"button": BUTTON}),
    "mouse_click": ("Press and release mouse button.", {"button": BUTTON}),
    "click_at": ("Click GUI only; screenshot coordinates normalized 0..1000, top-left origin.", XY),
    "type_text": ("Type into an already open text field.", {"text": {"type": "string", "maxLength": 2000}}),
    "wait": ("Let the game run for ms milliseconds.", {"ms": number(0, 10000)}),
}
SKILLS = {
    "move": ("Hold direction with optional concurrent sprint/jump/sneak; release all afterwards. Jump is held for the whole move; do not sprint+sneak together.", {"direction": choice("forward", "back", "left", "right"), "seconds": number(0, 10), "sprint": {"type": "boolean", "default": False}, "jump": {"type": "boolean", "default": False}, "sneak": {"type": "boolean", "default": False}}),
    "jump": ("Tap space for a jump.", {}),
    "look": ("RELATIVE yaw/pitch degrees, not absolute orientation: positive yaw right, pitch down.", {"yaw_deg": number(-360, 360), "pitch_deg": number(-180, 180)}),
    "attack": ("Hold left mouse to mine/attack; aim at a block within reach first.", {"seconds": number(0, 10)}),
    "use": ("Right click once.", {}),
    "select_hotbar": ("Select hotbar slot 1..9.", {"n": {"type": "integer", "minimum": 1, "maximum": 9}}),
    "open_inventory": ("Tap E. Only use if inventory is currently closed.", {}),
    "close_screen": ("Tap Escape. Only use if a GUI is open; otherwise it opens pause menu.", {}),
    "toggle_debug": ("Tap F3 to toggle Minecraft's vanilla debug screen; read it only from screenshots.", {}),
    "aim_at": ("Aim the crosshair at a full-screen point (0..1000) using FOV70 camera geometry, not game state. Approximate at steep pitch; check the next frame.", XY),
    "set_pitch": ("Set pitch -90 up .. +90 down by clamping with mouse input, then rotating back. Reads no orientation state.", {"deg": number(-90, 90)}),
    "mine_up": ("Look straight up, attempt count overhead blocks (up to 4 seconds each). Pixel-change heuristic only; verify drops visually.", {"count": {"type": "integer", "minimum": 1, "maximum": 4}}),
    "attack_until_broken": ("Hold attack up to max_s, sampling screenshots every 0.35s. Stop on a strong center-pixel change; returns broke/no change as a heuristic, NOT proof of a block break or pickup. All sampled frames are shown next turn.", {"max_s": number(.35, 10)}),
    "update_notes": ("Replace your own scratchpad (rung, visible inventory, landmarks, lessons). Injected every turn; empty text clears it. No game-state lookup.", {"text": {"type": "string", "maxLength": 4000}}),
    "click_at": RAW["click_at"],
    "shift_click_at": ("Shift-click a GUI slot at normalized screenshot coordinates.", XY),
    "wait": ("Let game ticks run for seconds.", {"seconds": number(0, 10)}),
}


def tool_schemas(tier):
    vocabulary = RAW if tier == "raw" else SKILLS
    return [{"type": "function", "function": {"name": name, "description": description + (" Harness returns frame_change/center_change (0..1 pixel differences) and stuck after two nearly unchanged moves. These are screenshot-only heuristics; source screenshots are shown next turn, never mod/game-state feedback." if tier == "skills" else ""),
             "parameters": {"type": "object", "properties": {**fields, "intent": {"type": "string", "minLength": 1, "maxLength": 500, "description": "One sentence explaining why you are taking this action."}},
                            "required": [*[key for key, spec in fields.items() if "default" not in spec], "intent"], "additionalProperties": False}}}
            for name, (description, fields) in vocabulary.items()]


def parse_calls(message, tier):
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list) or len(calls) > 16:
        raise InvalidToolArgs("Expected at most 16 tool calls")
    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in tool_schemas(tier)}
    parsed = []
    for call in calls:
        try:
            function = call["function"]
            name = function["name"]
            args = json.loads(function["arguments"]) if isinstance(function["arguments"], str) else function["arguments"]
            schema = schemas[name]
            if not isinstance(args, dict) or not set(schema["required"]) <= set(args) <= set(schema["properties"]):
                raise ValueError("Missing or extra arguments")
            for key, value in args.items():
                spec = schema["properties"][key]
                if spec["type"] in ("number", "integer"):
                    if type(value) not in (int, float) or not math.isfinite(value) or not spec["minimum"] <= value <= spec["maximum"]:
                        raise ValueError(f"Invalid {key}")
                    if spec["type"] == "integer" and type(value) is not int:
                        raise ValueError(f"Expected integer {key}")
                elif spec["type"] == "boolean":
                    if type(value) is not bool:
                        raise ValueError(f"Expected boolean {key}")
                elif not isinstance(value, str) or not spec.get("minLength", 0) <= len(value.strip() if key == "intent" else value) <= spec.get("maxLength", 10000):
                    raise ValueError(f"Invalid string {key}")
                if "enum" in spec and value not in spec["enum"]:
                    raise ValueError(f"Unknown {key}")
            if name == "move" and args.get("sprint") and args.get("sneak"):
                raise ValueError("Cannot sprint and sneak together")
            parsed.append({"name": name, "arguments": args})
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidToolArgs(f"Malformed tool call: {error}") from error
    return parsed


class ToolExecutor:
    def __init__(self, client, width, height):
        self.client, self.width, self.height = client, width, height
        self.held_keys, self.held_buttons = set(), set()
        self.actions = []
        self.results, self.frames = [], []
        self.current_frame = None
        self.notes = ""
        self.unchanged_moves = 0

    def begin_step(self, png=None):
        self.actions, self.results, self.frames = [], [], []
        self.current_frame = png

    async def observe(self):
        png = await self.client.screenshot()
        self.frames.append(png)
        self.current_frame = png
        return png

    async def set_pitch(self, degrees):
        await self.primitive("mouse", dx=0, dy=180 / .15)
        await self.primitive("mouse", dx=0, dy=(degrees - 90) / .15)

    async def attack_until_broken(self, seconds):
        reference = self.current_frame or await self.observe()
        deadline = time.monotonic() + seconds
        changed_twice = 0
        await self.button(0, True)
        try:
            while (remaining := deadline - time.monotonic()) > 0:
                await self.primitive("wait", ms=min(.35, remaining) * 1000)
                try:
                    frame = await asyncio.wait_for(self.observe(), max(.001, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    return "no change"
                observed = changes(reference, frame)
                changed_twice = changed_twice + 1 if observed["center_changed_fraction"] >= .7 and observed["center_change"] >= .08 else 0
                if changed_twice >= 2:
                    return "broke"
            return "no change"
        finally:
            await self.button(0, False)

    async def primitive(self, kind, **args):
        self.actions.append({"action": kind, **args})
        if kind == "wait":
            await asyncio.sleep(args["ms"] / 1000)
        elif kind == "key":
            await self.client.key(keycode(args["key"]), pressed=args["pressed"])
        elif kind == "mouse":
            await self.client.mouse(**args)
        elif kind == "type":
            await self.client.type_text(args["text"])

    async def key(self, key, pressed):
        (self.held_keys.add if pressed else self.held_keys.discard)(key)
        await self.primitive("key", key=key, pressed=pressed)

    async def button(self, button, pressed):
        (self.held_buttons.add if pressed else self.held_buttons.discard)(button)
        await self.primitive("mouse", button=button, pressed=pressed)

    async def tap(self, key, ms=100):
        await self.key(key, True)
        try:
            await self.primitive("wait", ms=ms)
        finally:
            await self.key(key, False)

    async def click(self, button=0, ms=100):
        await self.button(button, True)
        try:
            await self.primitive("wait", ms=ms)
        finally:
            await self.button(button, False)

    async def release(self, retain=False):
        keys, buttons = self.held_keys.copy(), self.held_buttons.copy()
        try:
            for key in keys:
                await self.key(key, False)
            for button in buttons:
                await self.button(button, False)
        finally:
            if retain:
                self.held_keys, self.held_buttons = keys, buttons

    async def restore(self):
        for key in self.held_keys.copy():
            await self.key(key, True)
        for button in self.held_buttons.copy():
            await self.button(button, True)

    async def execute(self, call, tier):
        if tier == "raw":
            return await self.execute_inputs(call, tier)
        before = self.current_frame or await self.observe()
        outcome = await self.execute_inputs(call, tier)
        delta = changes(before, await self.observe())
        if call["name"] == "move":
            self.unchanged_moves = self.unchanged_moves + 1 if delta["frame_change"] < .002 else 0
        elif delta["frame_change"] >= .002:
            self.unchanged_moves = 0
        result = {"name": call["name"], **delta, "stuck": call["name"] == "move" and self.unchanged_moves >= 2}
        if outcome is not None:
            result["outcome"] = outcome
        self.results.append(result)
        return result

    async def execute_inputs(self, call, tier):
        name, args = call["name"], call["arguments"]
        if name in ("key_down", "key_up"):
            await self.key(args["key"], name == "key_down")
        elif name == "key_press":
            await self.tap(args["key"], args["ms"])
        elif name == "mouse_move":
            await self.primitive("mouse", dx=args["dx"], dy=args["dy"])
        elif name in ("mouse_down", "mouse_up", "mouse_click"):
            button = 0 if args["button"] == "left" else 1
            if name == "mouse_click":
                await self.click(button)
            else:
                await self.button(button, name == "mouse_down")
        elif name in ("click_at", "shift_click_at"):
            if name == "shift_click_at":
                await self.key("shift", True)
            try:
                await self.primitive("mouse", x=round(args["x"] * (self.width - 1) / 1000), y=round(args["y"] * (self.height - 1) / 1000))
                await self.click()
            finally:
                if name == "shift_click_at":
                    await self.key("shift", False)
        elif name == "type_text":
            await self.primitive("type", text=args["text"])
        elif name == "move":
            keys = [{"forward": "w", "back": "s", "left": "a", "right": "d"}[args["direction"]]]
            keys += [key for flag, key in (("sprint", "ctrl"), ("jump", "space"), ("sneak", "shift")) if args.get(flag)]
            try:
                for key in keys:
                    await self.key(key, True)
                await self.primitive("wait", ms=args["seconds"] * 1000)
            finally:
                for key in reversed(keys):
                    await self.key(key, False)
        elif name == "look":
            await self.primitive("mouse", dx=args["yaw_deg"] / 0.15, dy=args["pitch_deg"] / 0.15)
        elif name == "attack":
            await self.click(ms=args["seconds"] * 1000)
        elif name == "use":
            await self.click(1)
        elif name == "jump":
            await self.tap("space", 250)
        elif name == "select_hotbar":
            await self.tap(str(args["n"]))
        elif name == "toggle_debug":
            await self.tap("f3")
        elif name == "aim_at":
            focal = self.height / 2 / math.tan(math.radians(35))
            x, y = (args["x"] / 1000 - .5) * (self.width - 1), (args["y"] / 1000 - .5) * (self.height - 1)
            await self.primitive("mouse", dx=math.degrees(math.atan2(x, focal)) / .15,
                                 dy=math.degrees(math.atan2(y, math.hypot(focal, x))) / .15)
        elif name == "set_pitch":
            await self.set_pitch(args["deg"])
        elif name == "attack_until_broken":
            return await self.attack_until_broken(args["max_s"])
        elif name == "mine_up":
            await self.set_pitch(-90)
            outcomes = []
            for _ in range(args["count"]):
                outcomes.append(await self.attack_until_broken(4))
            return outcomes
        elif name == "update_notes":
            self.notes = args["text"]
        elif name in ("open_inventory", "close_screen"):
            await self.tap("e" if name == "open_inventory" else "esc")
        elif name == "wait":
            await self.primitive("wait", ms=args["ms"] if tier == "raw" else args["seconds"] * 1000)
