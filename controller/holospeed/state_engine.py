"""State-aware skill orchestration; model latency never times physical controls."""

from __future__ import annotations

import asyncio
import time

from .bridge import BridgeError
from .engine import RunBudgetReached
from .game_state import GameState, item_counts
from .providers import ProviderError
from .skills import confirmed_milestones, goal_reached, next_skills


async def read_state(engine, transitioning: bool = False) -> GameState:
    deadline = time.monotonic() + (5 if transitioning else 0)
    while True:
        try:
            state = await engine.bounded(engine.bridge.game_state)
            break
        except BridgeError as error:
            if (
                not transitioning
                or str(error) != "Structured game state is stale"
                or time.monotonic() >= deadline
            ):
                raise
            await engine.bounded(asyncio.sleep, 0.2)
    if not state.is_fresh(time.time() * 1000):
        raise RuntimeError("Fresh game state is required before executing skills")
    engine.recorder.save_game_state(state.model_dump())
    if state.player is None or state.player.health <= 0:
        raise RuntimeError("Player is unavailable or dead")
    return state


def milestones(engine, state: GameState, *, summary: bool = True) -> None:
    if summary:
        engine.log(
            "state_summary",
            {
                "tick": state.tick,
                "inventory": item_counts(state),
                "dimension": state.dimension,
                "player": {
                    "position": state.player.position.model_dump(),
                    "health": state.player.health,
                    "hunger": state.player.hunger,
                },
            },
        )
    existing = {item["id"] for item in engine.state["milestones"]}
    for name in confirmed_milestones(state):
        if name not in existing:
            event = {
                "id": name,
                "elapsed_ms": engine.recorder.elapsed_ms,
                "verification": "game_state",
                "state_id": state.id,
                "tick": state.tick,
                "evidence": {
                    "inventory": item_counts(state),
                    "dimension": state.dimension,
                    "verified_steps": list(getattr(state.skill, "verified_steps", [])),
                },
            }
            engine.state["milestones"].append(event)
            engine.log("milestone", event)


async def capture(engine):
    observation = await engine.bounded(engine.bridge.observe)
    event = engine.recorder.save_observation(observation)
    engine.events.append(event)
    engine.state["frame_url"] = event["data"]["frame_url"]
    engine.state["metrics"]["observations"] += 1
    return observation


async def choose(engine, state, candidates, observation, provider):
    """A bounded model suggestion may only select an existing local candidate."""
    if provider is None:
        return candidates[0], "Recipe prerequisites determine the next step."
    context = {
        "run_goal": "Enter the Nether through a repaired and lit portal using normal survival mechanics"
        if engine.settings.run_goal == "nether"
        else "Acquire a stone pickaxe and stone axe using normal survival mechanics",
        "completed_milestones": engine.state["milestones"],
        "recent_skills": list(engine.recent),
        "failures": list(engine.failures),
    }
    remaining = engine.settings.max_run_seconds - engine.recorder.elapsed_ms / 1000
    if remaining <= 0:
        raise RunBudgetReached("Run time budget reached")
    try:
        result = await asyncio.wait_for(
            engine.strategist.choose(
                observation, state.model_dump(), candidates, context, provider=provider
            ),
            timeout=min(8, remaining),
        )
        if engine.recorder.elapsed_ms >= engine.settings.max_run_seconds * 1000:
            raise RunBudgetReached("Strategy arrived after run deadline")
        engine.timing(result.provider, result.latency_ms, result.usage)
        engine.log("model_call", result.model_dump())
        candidate = next(item for item in candidates if item["id"] == result.candidate_id)
        return candidate, result.rationale
    except (TimeoutError, ProviderError, StopIteration) as error:
        if engine.recorder.elapsed_ms >= engine.settings.max_run_seconds * 1000:
            raise RunBudgetReached("Run time budget reached during planning") from error
        if isinstance(error, ProviderError) and error.latency_ms > 0:
            engine.timing(provider, error.latency_ms, error.response.get("usage", {}))
        engine.state["metrics"]["errors"] += 1
        engine.log(
            "strategy_fallback",
            {
                "provider": provider,
                "reason": str(error) or "Strategy deadline reached",
                "candidate_id": candidates[0]["id"],
                "request": getattr(error, "request", {}),
                "response": getattr(error, "response", {}),
            },
        )
        return candidates[0], "Local prerequisite plan selected after a bounded strategy failure."


async def execute_skill(engine, candidate, generation):
    engine.log("skill_started", candidate)
    ack = await engine.bounded(engine.bridge.start_skill, candidate)
    engine.log("skill_ack", ack)
    if ack["status"] == "rejected":
        return False, str(ack.get("reason", "Skill rejected")), await read_state(engine)
    skill_id = ack.get("skill_id")
    deadline = time.monotonic() + candidate["max_ticks"] / 20 + 3
    previous_summary = None
    while generation == engine.generation and engine.state["status"] == "running":
        state = await read_state(engine, transitioning=candidate["skill"] == "portal")
        skill = state.skill
        current_summary = (
            tuple(sorted(item_counts(state).items())),
            state.dimension,
            skill.phase if skill else None,
        )
        milestones(engine, state, summary=current_summary != previous_summary)
        previous_summary = current_summary
        engine.state["keys"] = skill.keys or [] if skill else []
        engine.state["expected_effect"] = (
            f"{item_counts(state).get(candidate['item'], 0)}/{candidate['target_count']} "
            f"{candidate['item'].removeprefix('minecraft:')} · "
            f"{skill.phase if skill else 'verifying inventory'}"
        )
        if candidate["skill"] == "portal":
            engine.state["expected_effect"] = (
                f"{skill.phase if skill else 'verifying transfer'} · {state.dimension}"
            )
        engine.log(
            "skill_progress",
            {
                "candidate_id": candidate["id"],
                "tick": state.tick,
                "inventory_count": item_counts(state).get(candidate["item"], 0),
                "dimension": state.dimension,
                "skill": skill.model_dump() if skill else None,
            },
        )
        await engine.hud()
        engine.broadcast()
        matches = skill is not None and skill.skill_id == skill_id
        if candidate["skill"] == "portal" and state.dimension == "minecraft:the_nether":
            await engine.bridge.stop("Nether entry verified; release portal controls")
            return True, "Nether dimension verified", state
        terminal = ack["status"] == "completed" or (
            matches and not skill.active and skill.status in {"completed", "failed", "cancelled"}
        )
        if terminal:
            complete = (
                candidate["skill"] != "portal"
                and item_counts(state).get(candidate["item"], 0) >= candidate["target_count"]
            )
            reason = (
                "Inventory target verified"
                if complete
                else (skill.reason if skill and skill.reason else "Inventory postcondition not met")
            )
            return complete, reason, state
        if time.monotonic() >= deadline:
            await engine.bridge.stop("Skill wall-clock deadline reached")
            return False, "Skill wall-clock deadline reached", state
        await engine.bounded(asyncio.sleep, 0.2)
    raise asyncio.CancelledError


async def run_state_agent(engine) -> None:
    generation = engine.generation
    failures = 0
    blocked: set[tuple] = set()
    planned_phase = None
    try:
        while engine.state["status"] == "running" and generation == engine.generation:
            if engine.state["metrics"]["actions"] >= engine.settings.max_actions:
                raise RunBudgetReached("Completed skill budget reached")
            state = await read_state(engine)
            milestones(engine, state)
            if goal_reached(state, engine.settings.run_goal):
                await capture(engine)
                engine.state.update(
                    status="paused",
                    objective="Nether reached"
                    if engine.settings.run_goal == "nether"
                    else "Stone tools acquired",
                    expected_effect="Player presence confirmed in the Nether."
                    if engine.settings.run_goal == "nether"
                    else "Stone pickaxe and axe confirmed in inventory.",
                )
                engine.log(
                    "event",
                    {
                        "name": "goal_reached",
                        "verification": "game_state",
                        "inventory": item_counts(state),
                        "dimension": state.dimension,
                    },
                )
                break
            signature = tuple(sorted(item_counts(state).items()))
            candidates = [
                item
                for item in next_skills(
                    state,
                    engine.state["milestones"],
                    goal=engine.settings.run_goal,
                    declared_seed=engine.settings.declared_seed,
                )
                if (item["id"], signature) not in blocked
            ]
            if not candidates:
                raise RuntimeError(
                    "No eligible skill: inspect nearby resources, GUI, or blocked prerequisites"
                )
            observation = await capture(engine)
            phase = tuple(item["id"] for item in engine.state["milestones"])
            provider = (
                "holo" if planned_phase != phase or failures else ("jev" if engine.jev else None)
            )
            engine.state.update(
                objective=candidates[0]["reason"],
                strategy="Selecting the next verified resource prerequisite",
                expected_effect="Controls released during strategic planning",
            )
            engine.broadcast()
            candidate, rationale = await choose(engine, state, candidates, observation, provider)
            if generation != engine.generation or engine.state["status"] != "running":
                break
            # State may change during inference. Revalidate counts and prerequisites.
            state = await read_state(engine)
            eligible = next_skills(
                state,
                engine.state["milestones"],
                goal=engine.settings.run_goal,
                declared_seed=engine.settings.declared_seed,
            )
            if not any(item["id"] == candidate["id"] for item in eligible):
                engine.log(
                    "event", {"name": "stale_skill_discarded", "candidate_id": candidate["id"]}
                )
                continue
            planned_phase = phase
            engine.state.update(
                objective=candidate["reason"],
                strategy=rationale,
                action={
                    "skill": candidate["skill"],
                    "item": candidate["item"],
                    "target_count": candidate["target_count"],
                },
            )
            engine.log(
                "skill_decision",
                {
                    "provider": provider or "local",
                    "candidate": candidate,
                    "rationale": rationale,
                    "state_id": state.id,
                },
            )
            await engine.hud()
            success, reason, after = await execute_skill(engine, candidate, generation)
            if generation != engine.generation or engine.state["status"] != "running":
                break
            result = {
                "candidate_id": candidate["id"],
                "item": candidate["item"],
                "target_count": candidate["target_count"],
                "reason": reason,
                "inventory": item_counts(after),
                "state_id": after.id,
                "postcondition": candidate.get("postcondition", "inventory"),
                "expected_dimension": candidate.get("expected_dimension"),
                "dimension": after.dimension,
            }
            engine.recent.append(result)
            engine.state.update(keys=[], action=None)
            if success:
                failures = 0
                engine.state["metrics"]["actions"] += 1
                engine.log("skill_completed", result)
                milestones(engine, after)
            else:
                failures += 1
                blocked.add((candidate["id"], tuple(sorted(item_counts(after).items()))))
                engine.state["metrics"]["recoveries"] += 1
                engine.failures.append(result)
                engine.log("skill_failed", result)
                await engine.bridge.stop("Replan after a failed skill")
                if failures >= 3:
                    raise RuntimeError(
                        "Three consecutive skill failures; inspect recorded evidence"
                    )
            engine.checkpoint()
            engine.broadcast()
    except RunBudgetReached as error:
        engine.state.update(
            status="paused", expected_effect="Run budget reached; controls released."
        )
        engine.log("event", {"name": "run_budget_reached", "detail": str(error)})
    except asyncio.CancelledError:
        engine.log("event", {"name": "skill_loop_cancelled", "detail": "Pause or shutdown"})
        raise
    except Exception as error:  # noqa: BLE001 - always release controls on failure.
        engine.state.update(
            status="error", expected_effect="Inputs released; inspect the failure event."
        )
        engine.state["metrics"]["errors"] += 1
        engine.log("error", {"message": str(error), "class": type(error).__name__})
    finally:
        await engine.bridge.stop("State-aware agent loop stopped")
        engine.state.update(keys=[], action=None)
        engine.checkpoint()
        await engine.hud()
        engine.broadcast()
