from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Key = Literal[
    "forward",
    "back",
    "left",
    "right",
    "jump",
    "sneak",
    "sprint",
    "attack",
    "use",
    "inventory",
    "drop",
    "swap",
    "escape",
]


class Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    button: Literal["left", "right", "middle"] = "left"


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[Key] = Field(default_factory=list, max_length=13)
    duration_ticks: int = Field(default=6, ge=1, le=80)
    yaw_delta: float = Field(default=0, ge=-90, le=90, allow_inf_nan=False)
    pitch_delta: float = Field(default=0, ge=-60, le=60, allow_inf_nan=False)
    hotbar: int | None = Field(default=None, ge=1, le=9)
    cursor: Cursor | None = None
    scroll: int = Field(default=0, ge=-9, le=9)

    @model_validator(mode="after")
    def valid_keys(self) -> Action:
        if self.duration_ticks > 20 and not (
            self.keys == ["attack"]
            and self.yaw_delta == 0
            and self.pitch_delta == 0
            and self.hotbar is None
            and self.cursor is None
            and self.scroll == 0
        ):
            raise ValueError("Only stationary attack may exceed 20 ticks (maximum 80)")
        if len(self.keys) != len(set(self.keys)):
            raise ValueError("duplicate keys")
        for a, b in [("forward", "back"), ("left", "right")]:
            if a in self.keys and b in self.keys:
                raise ValueError(f"opposing keys: {a}, {b}")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=1, max_length=300)
    strategy: str = Field(min_length=1, max_length=1500)
    expected_effect: str = Field(min_length=1, max_length=500)
    action: Action
    milestone: Literal["wood", "crafting_table", "wooden_pickaxe", "stone_tools"] | None = None
    evidence: str = Field(default="", max_length=1000)


class Observation(BaseModel):
    id: str
    frame_id: str
    tick: int
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    image_base64: str
    mime_type: Literal["image/png", "image/jpeg"] = "image/png"
    screen_open: bool = False


class ModelResult(BaseModel):
    decision: Decision
    request: dict[str, Any]
    response: dict[str, Any]
    latency_ms: float
    usage: dict[str, Any] = Field(default_factory=dict)
    model: str
    completed: bool = False
