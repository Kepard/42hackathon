import asyncio
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .client import MinecraftClient
from .evaluator import LadderEvaluator


KEYS = {"escape": 256, "esc": 256, "enter": 257, "return": 257,
        "tab": 258, "backspace": 259, "space": 32, "shift": 340,
        "ctrl": 341, "left": 263, "right": 262, "up": 265, "down": 264, "f3": 292}


def keycode(name):
    lowered = name.lower()
    if lowered in KEYS:
        return KEYS[lowered]
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())
    raise ValueError(f"Unsupported key: {name}")


class MinecraftSession:
    """DesktopSession's lifecycle/screenshot/action subset, not a sandbox provider.

    Native-app installation, shell/files, CSS selectors and VM lifecycle deliberately
    aren't emulated. Use with the task decorators directly, not cb.run's sandbox factory.
    """

    def __init__(self, url="ws://127.0.0.1:8765", trace_dir=None):
        self.client = MinecraftClient(url)
        self.evaluator = LadderEvaluator()
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.tracer = None
        self.task_started = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self, config=None, headless=None):
        await self.client.connect()
        if self.trace_dir is not None:
            from cua_bench.tracing import Tracing
            self.tracer = Tracing(self)
            self.tracer.start()

    def record(self, event, data, images=None):
        if self.tracer is not None:
            self.tracer.record(event, data, images)

    async def screenshot(self):
        png = await self.client.screenshot()
        self.record("screenshot", {}, [png])
        return png

    async def state(self):
        state = await self.client.state()
        self.evaluator.update(state)
        self.record("state", state)
        return state

    async def execute_action(self, action):
        import cua_bench as cb

        if isinstance(action, (cb.ClickAction, cb.RightClickAction, cb.DoubleClickAction)):
            button = 1 if isinstance(action, cb.RightClickAction) else 0
            await self.client.click(action.x, action.y, button)
            if isinstance(action, cb.DoubleClickAction):
                await self.client.click(action.x, action.y, button)
        elif isinstance(action, cb.MoveToAction):
            await self.client.mouse(x=action.x, y=action.y)
        elif isinstance(action, cb.KeyAction):
            code = keycode(action.key)
            await self.client.key(code)
            await asyncio.sleep(0.1)
            await self.client.key(code, pressed=False)
        elif isinstance(action, cb.TypeAction):
            await self.client.type_text(action.text)
        elif isinstance(action, cb.WaitAction):
            await asyncio.sleep(action.seconds)
        elif isinstance(action, cb.DoneAction):
            pass
        else:
            raise NotImplementedError(f"MinecraftSession does not implement {type(action).__name__}")
        self.record("action", {"type": type(action).__name__, "value": asdict(action) if is_dataclass(action) else str(action)})

    async def close(self):
        try:
            if self.tracer is not None and self.tracer._rows:
                self.tracer.save_to_disk(str(self.trace_dir), save_pngs=True)
        finally:
            await self.client.close()

    async def run_command(self, *args, **kwargs):
        raise NotImplementedError("MinecraftSession intentionally does not expose shell access")
