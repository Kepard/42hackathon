"""Pixel/input-only tool vocabulary. No state API is exposed here."""

import asyncio
import json
import math
import time

from .session import KEYS, keycode
from .vision import changes, outline, outline_box, large_face_box, close_face, movement_danger, broken_caption, hotbar_change


class InvalidToolArgs(ValueError):
    pass


def number(low, high, description=""):
    return {"type": "number", "minimum": low, "maximum": high, "description": description}


def choice(*values):
    return {"type": "string", "enum": list(values)}


XY = {"x": number(0, 1000), "y": number(0, 1000)}
BUTTON = choice("left", "right", "middle")
KEY = choice(*sorted(set(KEYS) | set("abcdefghijklmnopqrstuvwxyz0123456789")))
RAW = {
    "key_down": ("Hold any keyboard key until key_up; hold modifiers/F3 first for combos (Ctrl+Q drops a stack, F swaps offhand, F1/F2/F5/F11, L/P/T/Tab). Use wait for ticks. Commands are blocked; no cheats. Smooth camera/unbound actions can be bound in vanilla Controls.", {"key": KEY}),
    "key_up": ("Release a held key.", {"key": KEY}),
    "key_press": ("Press and release a key for ms milliseconds.", {"key": KEY, "ms": number(0, 10000)}),
    "mouse_move": ("Relative look units: 0.15 degrees/unit; positive dx right, dy down.", {"dx": number(-2400, 2400), "dy": number(-1200, 1200)}),
    "mouse_down": ("Hold left mine/attack, right use/place or middle pick-block. In GUIs combine mouse_move_to then mouse_up for drag splitting; modifiers work.", {"button": BUTTON}),
    "mouse_up": ("Release mouse button.", {"button": BUTTON}),
    "mouse_click": ("Press and release mouse button.", {"button": BUTTON}),
    "mouse_move_to": ("Move GUI cursor to normalized screenshot coordinates; dragging held mouse buttons distributes slots. No world coordinate input.", XY),
    "scroll": ("Scroll wheel: hotbar/spectator selection in world or scroll open GUI. Positive vertical scrolls up.", {"vertical": number(-100, 100), "horizontal": {**number(-100, 100), "default": 0}}),
    "click_at": ("Click GUI at normalized screenshot coordinates. button left/right/middle; count2 double-clicks to gather a stack. Hold Shift/Ctrl/Alt or number keys over slots using key tools.", {**XY, "button": {**BUTTON, "default": "left"}, "count": {"type": "integer", "minimum": 1, "maximum": 2, "default": 1}}),
    "type_text": ("Type into an already open text field.", {"text": {"type": "string", "maxLength": 2000}}),
    "wait": ("Let the game run for ms milliseconds.", {"ms": number(0, 10000)}),
}
SKILLS = {
    **RAW,
    "approach_until_outline": ("Pixel reflex: walk toward the crosshair, checking thin dark rectangular selection edges every 0.2s; stop on an outline cue, two unchanged views, or max_s. False negatives/positives are possible; inspect returned frame. No game state.", {"max_s": number(.2, 10), "sprint": {"type": "boolean", "default": False}}),
    "mine": ("Pixel reflex: hold attack; release on a NEW vanilla English Block broken subtitle plus strong center change, or no visible progress after 0.8s, or max_s. A pixel cue is not semantic proof: verify bark/target and drops. Uses screenshots only.", {"max_s": number(.2, 30)}),
    "collect": ("Pixel reflex: step forward in 0.2s chunks, stop when hotbar slot pixels gain an item-like foreground or at max_s. Returns hotbar_changed, not item identity; visually confirm the log. Do not use near cliffs. No inventory lookup.", {"max_s": number(.2, 3)}),
    "move": ("Hold direction with optional concurrent sprint/jump/sneak; release all afterwards. Jump is held for the whole move; do not sprint+sneak together.", {"direction": choice("forward", "back", "left", "right"), "seconds": number(0, 10), "sprint": {"type": "boolean", "default": False}, "jump": {"type": "boolean", "default": False}, "sneak": {"type": "boolean", "default": False}}),
    "jump": ("Tap space for a jump.", {}),
    "look": ("RELATIVE yaw/pitch degrees, not absolute orientation: positive yaw right, pitch down.", {"yaw_deg": number(-360, 360), "pitch_deg": number(-180, 180)}),
    "attack": ("Combat/entity attack: hold left mouse without a block-face gate. For blocks use attack_until_broken, which enforces close standoff.", {"seconds": number(0, 10)}),
    "use": ("Right click once.", {}),
    "select_hotbar": ("Select hotbar slot 1..9.", {"n": {"type": "integer", "minimum": 1, "maximum": 9}}),
    "open_inventory": ("Tap E. Only use if inventory is currently closed.", {}),
    "close_screen": ("Tap Escape. Only use if a GUI is open; otherwise it opens pause menu.", {}),
    "toggle_debug": ("Tap F3 to toggle Minecraft's vanilla debug screen; read it only from screenshots.", {}),
    "aim_at": ("Aim at a full-screen point (0..1000) using FOV70 geometry, not game state. Approximate at steep pitch. Lower-right points warn about possible held-item confusion, not object identification. Check next frame.", XY),
    "set_pitch": ("Set pitch -90 up .. +90 down by clamping with mouse input, then rotating back. Reads no orientation state.", {"deg": number(-90, 90)}),
    "mine_up": ("Mine close overhead blocks, looking straight up; far faces return not_close without walking. Up to4s each; verify drops visually.", {"count": {"type": "integer", "minimum": 1, "maximum": 4}}),
    "mine_down": ("Mine close blocks directly below; dangerous, inspect ground first. Far vertical faces return not_close without walking. Up to4s each.", {"count": {"type": "integer", "minimum": 1, "maximum": 4}}),
    "attack_until_broken": ("Always walk right up to the block before mining (~1.5–2blocks, centred face width scaled by frame height). First take short forward steps up to4s; stuck/danger/uncertain target returns not_close. Then hold at most max_s. Scene change is NOT break/pickup proof.", {"max_s": number(.35, 10)}),
    "update_notes": ("Replace your own scratchpad (rung, visible inventory, landmarks, lessons). Injected every turn; empty text clears it. No game-state lookup.", {"text": {"type": "string", "maxLength": 4000}}),
    "click_at": RAW["click_at"],
    "shift_click_at": ("Shift-click a GUI slot at normalized screenshot coordinates.", XY),
    "type_text": RAW["type_text"],
    "wait": ("Let game ticks run for seconds.", {"seconds": number(0, 10)}),
}


JEV_SKILLS = {
    "seek": ("Always walk right up to a block (~1.5–2blocks; face width scaled by frame height), not edge of reach. Fast TypeSafe Jev on the same screenshots aims/walks. Stops: in_reach(close+outline), close_unconfirmed(close; mining must probe), not_visible/stuck/danger/timeout. Never attacks; no privileged state.", {"target": {"type": "string", "minLength": 1, "maxLength": 160}, "max_s": {**number(.2, 10), "default": 8}}),
}


def tool_schemas(tier, reflex="none"):
    vocabulary = RAW if tier == "raw" else SKILLS
    if tier == "skills" and reflex == "jev":
        vocabulary = {**vocabulary, **JEV_SKILLS}
        for legacy in ("approach_until_outline", "move", "aim_at", "mine", "attack"):
            vocabulary.pop(legacy)
        vocabulary["reposition"] = ("Retreat or sidestep to escape hazards or expose occluded targets. NOT for approaching blocks: call seek(target) for that.", {**SKILLS["move"][1], "direction": choice("back", "left", "right"), "seconds": number(0, 1)})
        vocabulary["collect"] = ("Pick up visible drops AFTER mining, never use for block approach (call seek). Step forward using hotbar pixel diffs, then ask fast TypeSafe Jev vision on the same screenshot whether the requested item icon is in the hotbar. Jev score is advisory, NOT collection proof; no inventory/state lookup.", {**SKILLS["collect"][1], "item": {"type": "string", "maxLength": 160, "default": "log"}})
        vocabulary["attack_until_broken"] = (SKILLS["attack_until_broken"][0] + " Close approach uses fast TypeSafe Jev on screenshots. ONLY once close, an absent outline permits a probe up to0.6s, bounded by max_s; continue ONLY on target-centre progress and release on break pixels. Probe use/success is logged. Otherwise seek first. Collect and confirm inventory pixels.", SKILLS["attack_until_broken"][1])
    return [{"type": "function", "function": {"name": name, "description": description + (" Harness returns frame_change/center_change (0..1 pixel differences) and stuck. These are screenshot-only heuristics; internal reflex captures are saved for audit, representative frames shown next turn. Never mod/game-state feedback." if tier == "skills" else ""),
             "parameters": {"type": "object", "properties": {**fields, "intent": {"type": "string", "minLength": 1, "maxLength": 500, "description": "One sentence explaining why you are taking this action."}},
                            "required": [*[key for key, spec in fields.items() if "default" not in spec], "intent"], "additionalProperties": False}}}
            for name, (description, fields) in vocabulary.items()]


def parse_calls(message, tier, reflex="none"):
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list) or len(calls) > 16:
        raise InvalidToolArgs("Expected at most 16 tool calls")
    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in tool_schemas(tier, reflex)}
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
            if args.get("sprint") and args.get("sneak"):
                raise ValueError("Cannot sprint and sneak together")
            parsed.append({"name": name, "arguments": args})
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidToolArgs(f"Malformed tool call: {error}") from error
    return parsed


def observation_prefix(calls, reflex="none"):
    if not isinstance(calls, list):
        return calls, []
    for index, raw in enumerate(calls):
        try:
            call = parse_calls({"tool_calls": [raw]}, "skills", reflex)[0]
        except InvalidToolArgs:
            return calls[:index + 1], calls[index + 1:]
        if call["name"] in {"seek", "attack", "mine", "attack_until_broken", "mine_up", "mine_down", "approach_until_outline", "collect"} or (
                call["name"] in ("move", "reposition") and call["arguments"]["seconds"] > .5):
            return calls[:index + 1], calls[index + 1:]
    return calls, []


class ToolExecutor:
    def __init__(self, client, width, height, jev=None, visibility_threshold=.5):
        self.client, self.width, self.height = client, width, height
        self.held_keys, self.held_buttons = set(), set()
        self.actions = []
        self.results, self.frames = [], []
        self.mining_probes = []
        self.close_checks = []
        self.seek_target = "block face under the crosshair"
        self.current_frame = None
        self.notes = ""
        self.unchanged_moves = 0
        self.failed_mining_frame = None
        self.jev, self.visibility_threshold = jev, visibility_threshold

    def begin_step(self, png=None):
        self.actions, self.results, self.frames = [], [], []
        self.mining_probes = []
        self.close_checks = []
        self.current_frame = png

    async def observe(self):
        png = await self.client.screenshot()
        self.frames.append(png)
        self.current_frame = png
        return png

    async def set_pitch(self, degrees):
        await self.primitive("mouse", dx=0, dy=180 / .15)
        await self.primitive("mouse", dx=0, dy=(degrees - 90) / .15)

    async def attack_until_broken(self, seconds, walk=True):
        if not await self.approach_close(walk=walk):
            self.mining_probes.append({"used": False, "succeeded": None, "skipped": "not_close"})
            return "not_close"
        reference = await self.observe() if self.jev is not None else self.current_frame or await self.observe()
        probe = {"used": self.jev is not None and not outline(reference), "succeeded": None}
        self.mining_probes.append(probe)
        if probe["used"] and seconds <= 0:
            probe.update(succeeded=False, error="no_time_budget")
            return "not_in_reach"
        caption = broken_caption(reference) if probe["used"] else 0
        def target_broke(frame, delta):
            return (delta["center_changed_fraction"] >= .7 and delta["center_change"] >= .08) or (
                delta["center_changed_fraction"] > .3 and delta["center_change"] > .04 and broken_caption(frame) > caption + 8)
        deadline = time.monotonic() + seconds
        scene_changed = False
        center_progress = 0
        await self.button(0, True)
        try:
            if probe["used"]:
                await self.primitive("wait", ms=min(.6, max(0, deadline - time.monotonic() - .1)) * 1000)
                try:
                    frame = await asyncio.wait_for(self.observe(), max(.001, min(.2, deadline - time.monotonic())))
                    observed = changes(reference, frame)
                    center_progress = observed["center_change"]
                    probe.update(succeeded=center_progress >= .002 and observed["center_changed_fraction"] >= .01,
                                 center_change=center_progress, center_changed_fraction=observed["center_changed_fraction"],
                                 frame_index=len(self.frames) - 1, broke_pixel_cue=target_broke(frame, observed))
                except asyncio.TimeoutError:
                    probe.update(succeeded=False, error="capture_timeout")
            while probe["succeeded"] is not False and not probe.get("broke_pixel_cue") and (remaining := deadline - time.monotonic()) > 0:
                if remaining <= .15:
                    await self.primitive("wait", ms=remaining * 1000)
                    break
                await self.primitive("wait", ms=min(.35, remaining - .15) * 1000)
                try:
                    frame = await asyncio.wait_for(self.observe(), max(.001, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                observed = changes(reference, frame)
                center_progress = max(center_progress, observed["center_change"])
                scene_changed |= observed["center_changed_fraction"] >= .7 and observed["center_change"] >= .08
                if probe["used"]:
                    probe["broke_pixel_cue"] = target_broke(frame, observed)
        finally:
            await self.button(0, False)
        settled = changes(reference, await self.observe())
        center_progress = max(center_progress, settled["center_change"])
        scene_changed |= settled["frame_change"] >= .002 or probe.get("broke_pixel_cue", False)
        self.failed_mining_frame = reference if seconds >= .35 and center_progress < .01 and probe["succeeded"] is not True else None
        if probe["succeeded"] is False:
            return "not_in_reach"
        return "scene_changed" if scene_changed else "no_change"

    async def seek(self, target, seconds):
        self.seek_target = target
        try:
            return await self._seek(target, seconds)
        finally:
            await self.release()
            await self.observe()

    async def _seek(self, target, seconds):
        if self.jev is None:
            raise InvalidToolArgs("seek requires reflex=jev")
        deadline = time.monotonic() + seconds
        anchor, walking_since = None, None
        while time.monotonic() < deadline:
            tick = time.monotonic()
            frame = await self.observe()
            try:
                if movement_danger(frame):
                    return "danger"
                async with asyncio.timeout(max(.001, deadline - time.monotonic())):
                    decision = await self.jev.ask(frame, target)
            except TimeoutError:
                return "timeout"
            except Exception:
                return "vision_error"
            decision["frame_index"] = len(self.frames) - 1
            if decision["noul"] < self.visibility_threshold:
                decision["decision"] = "not_visible"
                return "not_visible"
            l, t, r, b = decision["box"]
            x, y = (l + r) / 2, (t + b) / 2
            centered = abs(x - .5) <= min(.035, (r - l) * .2) and abs(y - .5) <= min(.04, (b - t) * .2)
            near_face = (r - l) * self.width >= 180 * self.height / 480
            previous_miss = self.failed_mining_frame is not None and changes(self.failed_mining_frame, frame)["center_change"] < .01
            if previous_miss and centered and near_face:
                decision.update(decision="stuck", reason="unchanged_mining_target_too_close")
                return "stuck"
            if not previous_miss and close_face(decision["box"], self.width, self.height) and outline(frame, decision["box"]):
                await self.primitive("wait", ms=min(100, max(0, deadline - time.monotonic()) * 1000))
                if outline(await self.observe(), decision["box"]):
                    decision["decision"] = "in_reach"
                    return "in_reach"
                decision["reach_unconfirmed"] = True
                continue
            if not previous_miss and close_face(decision["box"], self.width, self.height):
                decision.update(decision="close_unconfirmed", walking=False)
                return "close_unconfirmed"
            focal = self.height / 2 / math.tan(math.radians(35))
            px, py = (x - .5) * (self.width - 1), (y - .5) * (self.height - 1)
            yaw = max(-12, min(12, .7 * math.degrees(math.atan2(px, focal))))
            pitch = max(-8, min(8, .7 * math.degrees(math.atan2(py, math.hypot(focal, px)))))
            walking = centered and not near_face
            decision.update(decision="steer", yaw_deg=yaw, pitch_deg=pitch, walking=walking)
            await self.primitive("mouse", dx=yaw / .15, dy=pitch / .15)
            if walking:
                if walking_since is None or changes(anchor, frame)["frame_change"] >= .002:
                    anchor, walking_since = frame, time.monotonic()
                elif time.monotonic() - walking_since >= 1:
                    decision["decision"] = "stuck"
                    return "stuck"
                await self.key("w", True)
                try:
                    await self.primitive("wait", ms=max(0, min(.12, deadline - time.monotonic())) * 1000)
                finally:
                    await self.key("w", False)
            else:
                anchor, walking_since = None, None
            await asyncio.sleep(max(0, min(.2 - (time.monotonic() - tick), deadline - time.monotonic())))
        return "timeout"

    async def approach_close(self, walk=True):
        deadline, still = time.monotonic() + 4, 0
        owns_w = not any(keycode(key) == 87 for key in self.held_keys)
        try:
            while (remaining := deadline - time.monotonic()) > 0:
                frame = await asyncio.wait_for(self.observe(), remaining)
                danger = movement_danger(frame)
                if danger:
                    self.close_checks.append({"decision": "not_close", "reason": danger})
                    return False
                if self.jev is not None:
                    decision = await asyncio.wait_for(self.jev.ask(frame, self.seek_target), max(.001, deadline - time.monotonic()))
                    decision.update(decision="close_check", frame_index=len(self.frames) - 1)
                    box = decision.get("box") if decision.get("noul", 0) >= self.visibility_threshold else None
                else:
                    box = outline_box(frame) or large_face_box(frame)
                close = close_face(box, self.width, self.height)
                check = {"box": box, "close": close, "decision": "close" if close else "approach"}
                self.close_checks.append(check)
                if close:
                    return True
                if not walk or box is None or not (box[0] <= .5 <= box[2] and box[1] <= .5 <= box[3]):
                    check.update(decision="not_close", reason="no_centred_close_face")
                    return False
                if deadline - time.monotonic() < .2:
                    break
                if owns_w:
                    await self.key("w", True)
                await self.primitive("wait", ms=200)
                if owns_w:
                    await self.key("w", False)
                after = await self.observe()
                still = still + .2 if changes(frame, after)["frame_change"] < .002 else 0
                if still >= .8:
                    check.update(decision="not_close", reason="stuck")
                    return False
        except Exception:
            self.close_checks.append({"decision": "not_close", "reason": "vision_error"})
        finally:
            if owns_w and "w" in self.held_keys:
                await self.key("w", False)
        return False

    async def reflex(self, name, seconds, sprint=False):
        reference = self.current_frame or await self.observe()
        previous, unchanged = reference, 0
        caption = broken_caption(reference) if name == "mine" else 0
        if name == "approach_until_outline" and outline(reference):
            return "outline_pixel_cue"
        started = time.monotonic()
        try:
            if name == "mine":
                await self.button(0, True)
            else:
                await self.key("w", True)
                if sprint:
                    await self.key("ctrl", True)
            while (remaining := seconds - (time.monotonic() - started)) > 0:
                await self.primitive("wait", ms=min(.2, remaining) * 1000)
                try:
                    frame = await asyncio.wait_for(self.observe(), max(.25, seconds - (time.monotonic() - started)))
                except asyncio.TimeoutError:
                    return "timeout"
                delta = changes(reference, frame)
                if name == "mine":
                    current = broken_caption(frame)
                    if current > caption + 8 and delta["center_changed_fraction"] > .3 and delta["center_change"] > .04:
                        return "block_gone_pixel_cue"
                    caption = current
                    if time.monotonic() - started >= .8 and delta["center_change"] < .0008:
                        return "no_progress_cue"
                elif name == "collect":
                    if hotbar_change(reference, frame):
                        return "hotbar_changed"
                else:
                    if outline(frame):
                        return "outline_pixel_cue"
                    unchanged = unchanged + 1 if changes(previous, frame)["frame_change"] < .002 else 0
                    if unchanged >= 2:
                        return "stuck"
                previous = frame
            return "timeout"
        finally:
            if name == "mine":
                await self.button(0, False)
            else:
                await self.key("w", False)
                if sprint:
                    await self.key("ctrl", False)

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
        code = keycode(key)
        was_held = any(keycode(held) == code for held in self.held_keys)
        (self.held_keys.add if pressed else self.held_keys.discard)(key)
        is_held = any(keycode(held) == code for held in self.held_keys)
        if was_held != is_held:
            await self.primitive("key", key=key, pressed=is_held)

    async def button(self, button, pressed):
        (self.held_buttons.add if pressed else self.held_buttons.discard)(button)
        await self.primitive("mouse", button=button, pressed=pressed)

    async def tap(self, key, ms=100):
        already_held = key in self.held_keys
        await self.key(key, True)
        try:
            await self.primitive("wait", ms=ms)
        finally:
            if not already_held:
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
        for key in {keycode(name): name for name in self.held_keys}.values():
            await self.primitive("key", key=key, pressed=True)
        for button in self.held_buttons.copy():
            await self.button(button, True)

    async def execute(self, call, tier):
        probe_start = len(self.mining_probes)
        if tier == "raw":
            return await self.execute_inputs(call, tier)
        before = self.current_frame or await self.observe()
        outcome = await self.execute_inputs(call, tier)
        after = self.current_frame if call["name"] in ("seek", "attack_until_broken", "approach_until_outline", "mine", "collect") else await self.observe()
        delta = changes(before, after)
        moving = call["name"] in ("move", "reposition")
        if moving:
            self.unchanged_moves = self.unchanged_moves + 1 if delta["frame_change"] < .002 else 0
        elif delta["frame_change"] >= .002:
            self.unchanged_moves = 0
        result = {"name": call["name"], **delta, "stuck": outcome == "stuck" or (moving and self.unchanged_moves >= 2)}
        result.update(scene_changed=delta["frame_change"] >= .002 or outcome == "scene_changed",
                      block_gone=None, item_collected=None)
        if call["name"] in ("approach_until_outline", "mine", "collect"):
            result.update(outline_pixel_cue=outcome == "outline_pixel_cue",
                          block_gone_pixel_cue=outcome == "block_gone_pixel_cue",
                          hotbar_changed=outcome == "hotbar_changed")
        if outcome is not None:
            result["outcome"] = outcome
        if call["name"] == "attack_until_broken":
            result["scene_changed"] = outcome == "scene_changed"
        if len(self.mining_probes) > probe_start:
            result["mining_probes"] = self.mining_probes[probe_start:]
        if outcome in ("not_in_reach", "not_close"):
            result["next_action"] = "call seek(<target>) first"
        if self.close_checks:
            result["close_checks"] = list(self.close_checks)
        if self.jev is not None and call["name"] == "attack_until_broken" and outcome in ("scene_changed", "no_change") and self.failed_mining_frame is not None:
            result.update(mining_progress_pixel_cue=False, next_action="call seek(<target>) first")
        if call["name"] == "seek" and outcome == "stuck":
            result["next_action"] = "retreat or sidestep with reposition, then seek(<target>) again; or choose another target"
        if call["name"] == "collect" and self.jev is not None:
            try:
                advice = await self.jev.ask(after, call["arguments"].get("item", "log"), hotbar=True)
                advice["frame_index"] = len(self.frames) - 1
                result["hotbar_item_advisory"] = {"item": advice["target"], "noul": advice["noul"], "not_collection_proof": True}
            except Exception:
                result["hotbar_item_advisory"] = {"error": "vision_error", "not_collection_proof": True}
        if call["name"] == "aim_at" and call["arguments"]["x"] >= 700 and call["arguments"]["y"] >= 700:
            result["warning"] = "Aim point overlaps the usual held-item corner, not necessarily a world block. Select an EMPTY hotbar slot to check whether the object disappears before aiming again."
        self.results.append(result)
        return result

    async def execute_inputs(self, call, tier):
        name, args = call["name"], call["arguments"]
        if name == "reposition":
            return await self.execute_inputs({"name": "move", "arguments": args}, tier)
        if name == "seek":
            return await self.seek(args["target"], args.get("max_s", 8))
        if tier == "skills" and name == "mine" and not await self.approach_close():
            self.mining_probes.append({"used": False, "succeeded": None, "skipped": "not_close"})
            return "not_close"
        if name in ("approach_until_outline", "mine", "collect"):
            return await self.reflex(name, args["max_s"], args.get("sprint", False))
        if name in ("key_down", "key_up"):
            await self.key(args["key"], name == "key_down")
        elif name == "key_press":
            await self.tap(args["key"], args["ms"])
        elif name == "mouse_move":
            await self.primitive("mouse", dx=args["dx"], dy=args["dy"])
        elif name in ("mouse_down", "mouse_up", "mouse_click"):
            button = {"left": 0, "right": 1, "middle": 2}[args["button"]]
            if name == "mouse_click":
                await self.click(button)
            else:
                await self.button(button, name == "mouse_down")
        elif name == "scroll":
            await self.primitive("mouse", scroll_y=args["vertical"], scroll_x=args.get("horizontal", 0))
        elif name in ("click_at", "shift_click_at", "mouse_move_to"):
            add_shift = name == "shift_click_at" and not any(keycode(k) in (340, 344) for k in self.held_keys)
            if add_shift:
                await self.key("shift", True)
            try:
                await self.primitive("mouse", x=round(args["x"] * (self.width - 1) / 1000), y=round(args["y"] * (self.height - 1) / 1000))
                if name != "mouse_move_to":
                    for _ in range(args.get("count", 1)):
                        await self.click({"left": 0, "right": 1, "middle": 2}[args.get("button", "left")])
            finally:
                if add_shift:
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
        elif name in ("mine_up", "mine_down"):
            await self.set_pitch(-90 if name == "mine_up" else 90)
            await self.observe()
            outcomes = []
            for _ in range(args["count"]):
                outcomes.append(await self.attack_until_broken(4, walk=False))
                if outcomes[-1] == "not_close":
                    return "not_close"
            return outcomes
        elif name == "update_notes":
            self.notes = args["text"]
        elif name in ("open_inventory", "close_screen"):
            await self.tap("e" if name == "open_inventory" else "esc")
        elif name == "wait":
            await self.primitive("wait", ms=args["ms"] if tier == "raw" else args["seconds"] * 1000)
