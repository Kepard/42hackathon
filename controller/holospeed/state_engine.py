"""State-aware skill orchestration; model latency never times physical controls."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from .bridge import BridgeError
from .engine import RunBudgetReached
from .game_state import GameState, item_counts
from .providers import ProviderError, ReplanRequired
from .skills import confirmed_milestones, goal_reached, next_skills

TERMINAL_PAUSE_TIMEOUT_S = 5.0


async def pause_world_after_terminal(engine, reason: str) -> None:
    """Freeze a failed/budget-limited live world without reentering engine.lock."""
    if engine.settings.mode != "live":
        return
    if not hasattr(engine.bridge, "pause_game"):
        engine.log(
            "event",
            {
                "name": "world_pause_unavailable",
                "after": reason,
                "detail": "This bridge does not support pausing the world",
            },
        )
        return
    try:
        # The run budget is already exhausted on this path. Give cleanup its
        # own short deadline instead of routing it through engine.bounded().
        paused = await asyncio.wait_for(
            engine.bridge.pause_game(), timeout=TERMINAL_PAUSE_TIMEOUT_S
        )
    except Exception as error:  # noqa: BLE001 - never replace the original run failure.
        engine.log(
            "event",
            {
                "name": "world_pause_unavailable",
                "after": reason,
                "class": type(error).__name__,
                "detail": "World pause acknowledgment timed out"
                if isinstance(error, TimeoutError)
                else str(error)[:300],
            },
        )
        return
    engine.log("event", {"name": "world_paused_after_terminal", "after": reason, "paused": paused})
    if paused:
        engine.state["expected_effect"] += " World paused."


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
    for name in confirmed_milestones(
        state, goal=engine.settings.run_goal, route=engine.settings.nether_route
    ):
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


@dataclass(frozen=True)
class SkillChoice:
    candidate: dict
    rationale: str
    provider: str
    request_id: str | None = None
    model: str | None = None


async def choose(engine, state, candidates, observation, provider, *, extra_context=None):
    """A bounded model suggestion may only select an existing local candidate."""
    if provider is None:
        return SkillChoice(candidates[0], "Recipe prerequisites determine the next step.", "local")
    context = {
        "run_goal": (
            "Gather wood, craft tools, mine and smelt iron, then bucket-cast a complete Nether portal "
            "from natural lava and water. No structure loot or existing portal frames. Enter using normal survival mechanics."
            if engine.settings.nether_route == "from_scratch"
            else "Enter the Nether through a repaired and lit portal using normal survival mechanics"
        )
        if engine.settings.run_goal == "nether"
        else "Acquire a stone pickaxe and stone axe using normal survival mechanics",
        "completed_milestones": engine.state["milestones"],
        "recent_skills": list(engine.recent),
        "failures": list(engine.failures),
        "operator_guidance": list(engine.operator_guidance),
        **(extra_context or {}),
    }
    remaining = engine.settings.max_run_seconds - engine.recorder.elapsed_ms / 1000
    if remaining <= 0:
        raise RunBudgetReached("Run time budget reached")
    generation = engine.generation
    request_id = str(uuid.uuid4())
    context["inference_request_id"] = request_id
    model = engine.settings.holo_model if provider == "holo" else engine.settings.jev_model
    validating = context.get("decision_role") == "fresh_state_validation"
    task = (
        f"Check fresh-state eligibility: {candidates[0]['reason']}"
        if validating
        else f"Select next skill: {candidates[0]['reason']}"
    )[:240]
    activity = {
        "provider": provider,
        "request_id": request_id,
        "task": task,
        "model": model,
        "input_source": "screenshot_and_state"
        if provider == "holo" and observation is not None
        else "state",
        "decision_role": "fresh_state_validation" if validating else "skill_selection",
        "state_id": state.id,
    }
    started = time.monotonic()
    timeout = min(8, remaining)
    response_recorded = False

    def terminal(phase, **data):
        engine.log("inference", {**activity, "phase": phase, **data})
        engine.broadcast()

    def failed_call(error):
        latency = getattr(error, "latency_ms", 0) or (time.monotonic() - started) * 1000
        response = getattr(error, "response", {})
        if not response_recorded:
            engine.timing(provider, latency, response.get("usage", {}))
            engine.log(
                "model_call",
                {
                    "provider": provider,
                    "model": model,
                    "inference_request_id": request_id,
                    "latency_ms": latency,
                    "error": str(error) or "Strategy deadline reached",
                    "request": getattr(error, "request", {}),
                    "response": response,
                },
            )
        terminal(
            "failed",
            reason=(str(error) or "Strategy deadline reached")[:300],
            latency_ms=latency,
            rejected=isinstance(error, ReplanRequired),
        )

    engine.log("inference", {**activity, "phase": "thinking"})
    engine.broadcast()
    try:
        await engine.hud()
        if generation != engine.generation or engine.state["status"] != "running":
            raise asyncio.CancelledError
        remaining = engine.settings.max_run_seconds - engine.recorder.elapsed_ms / 1000
        if remaining <= 0:
            raise RunBudgetReached("Run time budget reached before model dispatch")
        timeout = min(8, remaining)
        started = time.monotonic()
        result = await asyncio.wait_for(
            engine.strategist.choose(
                observation, state.model_dump(), candidates, context, provider=provider
            ),
            timeout=timeout,
        )
        engine.timing(result.provider, result.latency_ms, result.usage)
        engine.log("model_call", {**result.model_dump(), "inference_request_id": request_id})
        response_recorded = True
        if generation != engine.generation or engine.state["status"] != "running":
            raise asyncio.CancelledError
        if engine.recorder.elapsed_ms >= engine.settings.max_run_seconds * 1000:
            raise RunBudgetReached("Strategy arrived after run deadline")
        if time.monotonic() - started >= timeout:
            raise TimeoutError("Strategy exceeded its decision deadline")
        candidate = next(item for item in candidates if item["id"] == result.candidate_id)
        terminal(
            "complete",
            rationale=result.rationale,
            latency_ms=result.latency_ms,
            candidate_id=result.candidate_id,
            model=result.model,
        )
        return SkillChoice(candidate, result.rationale, provider, request_id, result.model)
    except asyncio.CancelledError:
        terminal(
            "failed",
            reason="Inference cancelled; controls remain released",
            cancelled=True,
            latency_ms=(time.monotonic() - started) * 1000,
        )
        raise
    except RunBudgetReached as error:
        terminal(
            "failed",
            reason=str(error),
            cancelled=True,
            latency_ms=(time.monotonic() - started) * 1000,
        )
        raise
    except ReplanRequired as error:
        failed_call(error)
        raise
    except (TimeoutError, ProviderError, StopIteration) as error:
        failed_call(error)
        if engine.recorder.elapsed_ms >= engine.settings.max_run_seconds * 1000:
            raise RunBudgetReached("Run time budget reached during planning") from error
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
        return SkillChoice(
            candidates[0],
            "Local prerequisite plan selected after a bounded strategy failure.",
            "local",
            request_id,
        )
    except Exception as error:
        terminal(
            "failed",
            reason=f"Unexpected {type(error).__name__} during model selection",
            latency_ms=(time.monotonic() - started) * 1000,
        )
        raise


def eligible_candidate(engine, state, identifier):
    return next(
        (
            item
            for item in next_skills(
                state,
                engine.state["milestones"],
                goal=engine.settings.run_goal,
                declared_seed=engine.settings.declared_seed,
                route=engine.settings.nether_route,
                route_target=engine.settings.route_target,
            )
            if item["id"] == identifier
        ),
        None,
    )


async def execute_skill(engine, candidate, generation):
    if generation != engine.generation or engine.state["status"] != "running":
        raise asyncio.CancelledError
    engine.log("skill_started", candidate)
    ack = await engine.bounded(engine.bridge.start_skill, candidate)
    engine.log("skill_ack", ack)
    if ack["status"] == "rejected":
        return False, str(ack.get("reason", "Skill rejected")), await read_state(engine)
    skill_id = ack.get("skill_id")
    deadline = time.monotonic() + candidate["max_ticks"] / 20 + 3
    previous_summary = None
    while generation == engine.generation and engine.state["status"] == "running":
        portal = candidate["skill"] in {"portal", "cast_portal"}
        state = await read_state(engine, transitioning=portal)
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
        if portal:
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
        if portal and state.dimension == "minecraft:the_nether":
            await engine.bridge.stop("Nether entry verified; release portal controls")
            if candidate["skill"] == "cast_portal" and (
                not matches
                or skill.kind != "cast_portal"
                or not {"portal_built", "portal_lit"}.issubset(skill.verified_steps)
            ):
                return (
                    False,
                    "Nether reached without this skill's verified full-frame construction",
                    state,
                )
            return True, "Nether dimension verified", state
        terminal = ack["status"] == "completed" or (
            matches and not skill.active and skill.status in {"completed", "failed", "cancelled"}
        )
        if terminal:
            complete = (
                not portal
                and item_counts(state).get(candidate["item"], 0) >= candidate["target_count"]
                and (
                    candidate["skill"] != "smelt"
                    or ack["status"] == "completed"
                    or (matches and skill.status == "completed")
                )
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
    rejections = 0
    blocked: set[tuple] = set()
    planned_phase = None
    terminal_reason = None
    try:
        while engine.state["status"] == "running" and generation == engine.generation:
            if engine.state["metrics"]["actions"] >= engine.settings.max_actions:
                raise RunBudgetReached("Completed skill budget reached")
            state = await read_state(engine)
            milestones(engine, state)
            if goal_reached(state, engine.settings.run_goal):
                if (
                    engine.settings.run_goal == "nether"
                    and engine.settings.nether_route == "from_scratch"
                    and (
                        not engine.completed_cast_proof
                        or engine.completed_cast_proof["run_id"] != engine.recorder.id
                    )
                ):
                    raise RuntimeError(
                        "Nether present without verified full-frame construction in this recording"
                    )
                engine.freeze_timer("goal_reached")
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
                if engine.settings.mode == "live" and hasattr(engine.bridge, "pause_game"):
                    try:
                        paused = await engine.bridge.pause_game()
                        engine.log("event", {"name": "world_paused_after_goal", "paused": paused})
                    except BridgeError as error:
                        engine.log(
                            "event", {"name": "world_pause_unavailable", "detail": str(error)}
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
                    route=engine.settings.nether_route,
                    route_target=engine.settings.route_target,
                )
                if (item["id"], signature) not in blocked
            ]
            if not candidates:
                raise RuntimeError(
                    "No eligible skill: inspect nearby resources, GUI, or blocked prerequisites"
                )
            observation = await capture(engine)
            if generation != engine.generation or engine.state["status"] != "running":
                break
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
            validation = None
            candidate = candidates[0]
            source_state_id = state.id
            try:
                selection = await choose(engine, state, candidates, observation, provider)
                candidate, rationale = selection.candidate, selection.rationale
                if generation != engine.generation or engine.state["status"] != "running":
                    break
                # Recompute the candidate against state sampled AFTER Holo/Jev inference.
                state = await read_state(engine)
                if generation != engine.generation or engine.state["status"] != "running":
                    break
                fresh = eligible_candidate(engine, state, candidate["id"])
                if fresh is None:
                    engine.log(
                        "event", {"name": "stale_skill_discarded", "candidate_id": candidate["id"]}
                    )
                    continue
                candidate = fresh
                if provider == "holo" and engine.jev and engine.settings.validate_holo_plans:
                    validation = await choose(
                        engine,
                        state,
                        [candidate],
                        None,
                        "jev",
                        extra_context={
                            "decision_role": "fresh_state_validation",
                            "proposal_origin": selection.provider,
                            "previous_holo_plan"
                            if selection.provider == "holo"
                            else "previous_local_proposal": {
                                "candidate_id": selection.candidate["id"],
                                "rationale": selection.rationale,
                                "request_id": selection.request_id,
                                "source_state_id": source_state_id,
                                "source_frame_id": observation.frame_id,
                            },
                        },
                    )
                    if validation.provider != "jev" and rejections:
                        # A later outage is not new evidence overturning an
                        # explicit veto. Require a new actual Jev acceptance.
                        raise ReplanRequired(
                            "Jev unavailable after an explicit veto; fresh model approval is required",
                            provider="jev",
                            model=engine.settings.jev_model,
                        )
                    if generation != engine.generation or engine.state["status"] != "running":
                        break
                    state = await read_state(engine)
                    if generation != engine.generation or engine.state["status"] != "running":
                        break
                    fresh = eligible_candidate(engine, state, validation.candidate["id"])
                    if fresh is None:
                        engine.log(
                            "event",
                            {"name": "stale_skill_discarded", "candidate_id": candidate["id"]},
                        )
                        continue
                    candidate = fresh
                    rationale += (
                        " Jev checked fresh-state eligibility."
                        if validation.provider == "jev"
                        else " Jev unavailable; local preconditions rechecked."
                    )
            except ReplanRequired as error:
                rejections += 1
                planned_phase = None
                rejection = {
                    "provider": "jev",
                    "candidate_id": candidate["id"],
                    "reason": str(error),
                    "state_id": state.id,
                    "kind": "unexecuted_skill_rejection",
                    "consecutive_rejections": rejections,
                }
                engine.failures.append(rejection)
                engine.state["metrics"]["recoveries"] += 1
                engine.log("strategy_rejected", rejection)
                await engine.bridge.stop("Replan after explicit Jev skill rejection")
                engine.broadcast()
                if rejections >= 8:
                    raise RuntimeError(
                        "Eight consecutive Jev skill rejections; inspect recorded evidence"
                    ) from error
                await engine.bounded(asyncio.sleep, 0.2)
                continue
            rejections = 0
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
                    "provider": selection.provider,
                    "inference_request_id": selection.request_id,
                    "validation_provider": validation.provider if validation else None,
                    "validation_request_id": validation.request_id if validation else None,
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
                if candidate["skill"] == "cast_portal":
                    engine.completed_cast_proof = {
                        "run_id": engine.recorder.id,
                        "state_id": after.id,
                        "skill_id": after.skill.skill_id,
                    }
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
        terminal_reason = "run_budget_reached"
        engine.freeze_timer(terminal_reason)
        engine.state.update(
            status="paused", expected_effect="Run budget reached; controls released."
        )
        engine.log("event", {"name": "run_budget_reached", "detail": str(error)})
    except asyncio.CancelledError:
        engine.log("event", {"name": "skill_loop_cancelled", "detail": "Pause or shutdown"})
        raise
    except Exception as error:  # noqa: BLE001 - always release controls on failure.
        terminal_reason = "error"
        engine.freeze_timer(terminal_reason)
        engine.state.update(
            status="error", expected_effect="Inputs released; inspect the failure event."
        )
        engine.state["metrics"]["errors"] += 1
        engine.log("error", {"message": str(error), "class": type(error).__name__})
    finally:
        try:
            await engine.bridge.stop("State-aware agent loop stopped")
        except Exception as error:  # noqa: BLE001 - keep primary error/cancellation intact.
            engine.log(
                "event",
                {
                    "name": "controls_release_unavailable",
                    "class": type(error).__name__,
                    "detail": str(error)[:300],
                },
            )
        engine.state.update(keys=[], action=None)
        # Cancellation is also used by the launcher while unloading a world.
        # Only a terminal failure or exhausted budget should add a world pause.
        if terminal_reason is not None:
            await pause_world_after_terminal(engine, terminal_reason)
        engine.checkpoint()
        await engine.hud()
        engine.broadcast()
