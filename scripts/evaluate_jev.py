"""Compare a recorded Jev question with an action-specific, binary verifier.

Offline by default. --submit makes exactly one Jev request using the selected
recorded image. It never connects to Minecraft or executes an action.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import time
from pathlib import Path

import httpx
from holospeed.config import Settings
from holospeed.models import Action


def recorded_call(run_dir: Path, response_seq: int) -> tuple[dict, dict]:
    requests = {}
    for line in (run_dir / "events.jsonl").read_text().splitlines():
        event = json.loads(line)
        data = event["data"]
        if event["type"] == "model_request" and data.get("provider") == "jev":
            requests[data["call_id"]] = data["request"]
        if event["seq"] == response_seq and event["type"] == "model_response":
            if data.get("provider") != "jev":
                raise ValueError("Selected response is not a Jev call")
            return requests[data["call_id"]], data["response"]
    raise ValueError("Jev response sequence was not found")


def focused_request(recorded: dict) -> dict:
    old = recorded["state"]
    action = Action.model_validate(old["candidates"]["continue"], strict=True)
    camera_only = not action.keys and bool(action.yaw_delta or action.pitch_delta)
    no_input = (
        not action.keys
        and not camera_only
        and not action.cursor
        and not action.scroll
        and not action.hotbar
    )
    attack = action.keys == ["attack"]
    kind = (
        "camera_only"
        if camera_only
        else "observation_only"
        if no_input
        else "stationary_attack"
        if attack
        else "movement_or_other"
    )
    mechanical_effect = {
        "camera_only": "Rotate the view by the exact yaw/pitch delta without moving, attacking, or using items. Whether an unseen tree appears is not part of this action's validity.",
        "observation_only": "Keep every control released for this duration, then observe again. This action cannot reveal a hidden tree by itself; judge only holding still.",
        "stationary_attack": "Attack the block at the crosshair for exactly the stated duration without movement. For leaf clearing, leaves are the intended target rather than an obstruction to reject. Do not infer that wood will be collected.",
        "movement_or_other": "Perform exactly the bounded inputs once. Judge the immediately visible traversable direction or interaction target, not arrival at the eventual objective.",
    }[kind]
    return {
        "model": recorded["model"],
        "assets": copy.deepcopy(recorded["assets"]),
        "state": {
            "screenshot": old["screenshot"],
            "screen_open": old["screen_open"],
            "action_kind": kind,
            "exact_action": action.model_dump(),
            "mechanical_effect": mechanical_effect,
            "proposed_local_goal": old["objective"],
            "execution_limits": "20Hz. At most20ticks except stationary attack-only<=80ticks. No action mutation or world-state data.",
        },
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "Validate ONE exact action on the current screenshot. This is a bounded visual experiment, not a guarantee of the entire stated goal. Evaluate its mechanical effect. Do not split probability between two different safe durations; there is only this action or replan.",
                "criteria": {
                    "continue": "The exact action is a valid bounded next step with sufficient visible evidence for its control type. A stationary camera turn may inspect an unknown direction; it need not promise a tree. For attack, a usable target at the crosshair is required.",
                    "replan": "A concrete visible hazard, incompatible screen/control type, or invalid/insufficiently grounded interaction target prevents this exact step. Do not reject a camera-only scan merely because the destination view is unknown.",
                },
            },
            "danger": {
                "type": "noul",
                "instructions": "Does the screenshot show immediate danger from lava, falling, drowning, fire, death, or a nearby attacking enemy?",
            },
            "can_continue": {
                "type": "noul",
                "instructions": "Does the screenshot support this action's stated MECHANICAL effect for this control type? Camera-only turns require an active gameplay view, not proof of an unseen object. Attack requires an appropriate visible crosshair target. Movement requires a sufficiently safe immediate direction. Do not require the overall objective to finish.",
            },
            "crosshair_target": {
                "type": "choice",
                "instructions": "Classify what the center crosshair is pointing at. Report visual evidence independently of the proposed goal; do not assume the planner's label is correct.",
                "criteria": {
                    "leaves": "Leaf blocks appear under the crosshair.",
                    "log": "A wood log or tree trunk appears under the crosshair.",
                    "other_block": "Another block material appears under the crosshair.",
                    "air_or_distant": "The crosshair points into air or a clearly distant target.",
                    "uncertain": "The image does not support a confident target classification.",
                },
            },
        },
    }


def binary_request(recorded: dict) -> dict:
    """Ablation: change only the competing careful candidate, not prompt context."""
    result = copy.deepcopy(recorded)
    result["state"]["candidates"].pop("careful", None)
    result["questions"]["action"]["criteria"].pop("careful", None)
    return result


def summarize(response: dict) -> dict:
    answers = response.get("answers", {})
    choice = answers.get("action", {})
    selected = choice.get("choice")
    probability = choice.get("probabilities", {}).get(selected, 0)
    danger = answers.get("danger", {}).get("noul", 1)
    continuation = answers.get("can_continue", {}).get("noul", 0)
    accepted = (
        selected != "replan" and probability >= 0.65 and danger < 0.35 and continuation >= 0.70
    )
    return {
        "accepted_by_unchanged_thresholds": accepted,
        "answers": answers,
        "usage": response.get("usage", {}),
    }


async def submit(body: dict) -> dict:
    settings = Settings.from_env()
    if not settings.jev_api_key:
        raise ValueError("JEV_API_KEY is required")
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.post(
            "https://api.typesafe.ai/v1/systemone",
            json=body,
            headers={
                "Authorization": "Bearer " + settings.jev_api_key,
                "TypeSafe-Danger-Zone-Images": "true",
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"Jev returned HTTP {response.status_code}")
    result = summarize(response.json())
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("response_seq", type=int)
    parser.add_argument("--variant", choices=["recorded", "binary", "focused"], default="binary")
    parser.add_argument(
        "--submit", action="store_true", help="Make exactly one paid Jev request; no game controls"
    )
    args = parser.parse_args()
    recorded, response = recorded_call(args.run_dir, args.response_seq)
    body = (
        focused_request(recorded)
        if args.variant == "focused"
        else binary_request(recorded)
        if args.variant == "binary"
        else recorded
    )
    if args.submit:
        print(json.dumps(asyncio.run(submit(body)), indent=2))
    else:
        print(
            json.dumps(
                {
                    "historical_result": summarize(response),
                    "proposed_questions": body["questions"],
                    "variant": args.variant,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
