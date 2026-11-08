"""Actual planner activity and fresh-state Holo-to-Jev skill handoff."""

import asyncio

import pytest
from holospeed.providers import ProviderError, ReplanRequired
from test_state_runtime import (
    FirstCandidateStrategist,
    PortalBridge,
    SkillBridge,
    engine_with_fakes,
    run_events,
)


async def test_holo_plan_gets_real_jev_fresh_state_validation_before_portal_skill(tmp_path):
    engine, bridge, strategist = await engine_with_fakes(
        tmp_path,
        PortalBridge(),
        jev_api_key="unused-test-jev",
        run_goal="nether",
        nether_route="ruined_portal",
        declared_seed="-1950884254474434485",
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        assert [call["provider"] for call in strategist.calls] == ["holo", "jev"]
        plan, validation = strategist.calls
        assert validation["state"]["tick"] > plan["state"]["tick"]
        assert len(validation["candidates"]) == 1
        assert validation["candidates"][0]["id"] == plan["candidates"][0]["id"]
        assert validation["context"]["decision_role"] == "fresh_state_validation"
        prior = validation["context"]["previous_holo_plan"]
        assert prior["source_state_id"] == plan["state"]["id"]
        assert prior["rationale"] == "Choose the verified next prerequisite"
        events = run_events(engine)
        inference = [event for event in events if event["type"] == "inference"]
        assert [(event["data"]["provider"], event["data"]["phase"]) for event in inference] == [
            ("holo", "thinking"),
            ("holo", "complete"),
            ("jev", "thinking"),
            ("jev", "complete"),
        ]
        assert (
            inference[0]["data"]["request_id"]
            == inference[1]["data"]["request_id"]
            == prior["request_id"]
        )
        assert inference[2]["data"]["request_id"] == inference[3]["data"]["request_id"]
        assert inference[0]["data"]["request_id"] != inference[2]["data"]["request_id"]
        assert inference[0]["data"]["input_source"] == "screenshot_and_state"
        assert inference[2]["data"]["input_source"] == "state"
        assert inference[3]["seq"] < next(
            event["seq"] for event in events if event["type"] == "skill_started"
        )
        decision = next(event["data"] for event in events if event["type"] == "skill_decision")
        assert decision["validation_provider"] == "jev"
        assert decision["validation_request_id"] == inference[3]["data"]["request_id"]
        assert decision["state_id"] != validation["state"]["id"]  # Rechecked AFTER Jev too.
        assert len(bridge.started) == 1 and engine.state["metrics"]["actions"] == 1
        assert all("thinking_trace" not in event["data"] for event in inference)
    finally:
        await engine.close()


async def test_disabled_validation_has_no_invented_jev_activity(tmp_path):
    engine, _, strategist = await engine_with_fakes(
        tmp_path,
        PortalBridge(),
        jev_api_key="unused-test-jev",
        validate_holo_plans=False,
        run_goal="nether",
        nether_route="ruined_portal",
        declared_seed="-1950884254474434485",
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        assert [call["provider"] for call in strategist.calls] == ["holo"]
        inference = [event["data"] for event in run_events(engine) if event["type"] == "inference"]
        assert len(inference) == 2 and {event["provider"] for event in inference} == {"holo"}
    finally:
        await engine.close()


class RejectingJev(FirstCandidateStrategist):
    def __init__(self, error_type):
        super().__init__()
        self.error_type = error_type

    async def choose(self, observation, state, candidates, context, provider="holo"):
        result = await super().choose(observation, state, candidates, context, provider)
        if provider == "jev":
            raise self.error_type(
                "Jev requested skill replanning"
                if self.error_type is ReplanRequired
                else "Service unavailable",
                latency_ms=2,
                provider="jev",
                model="test-jev",
            )
        return result


async def test_explicit_jev_veto_never_falls_back_to_execution_and_stops_after_eight(tmp_path):
    engine, bridge, strategist = await engine_with_fakes(
        tmp_path,
        strategist=RejectingJev(ReplanRequired),
        jev_api_key="unused-test-jev",
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        assert bridge.started == []
        assert engine.state["status"] == "error"
        assert len(strategist.calls) == 16
        events = run_events(engine)
        assert len([event for event in events if event["type"] == "strategy_rejected"]) == 8
        assert not any(event["type"] == "strategy_fallback" for event in events)
        rejected = [
            event["data"]
            for event in events
            if event["type"] == "inference" and event["data"].get("rejected")
        ]
        assert len(rejected) == 8
        assert all(event["phase"] == "failed" and event["provider"] == "jev" for event in rejected)
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["metrics"]["recoveries"] == 8
    finally:
        await engine.close()


async def test_jev_service_failure_is_labeled_and_uses_revalidated_local_fallback(tmp_path):
    engine, bridge, _ = await engine_with_fakes(
        tmp_path,
        PortalBridge(),
        strategist=RejectingJev(ProviderError),
        jev_api_key="unused-test-jev",
        run_goal="nether",
        nether_route="ruined_portal",
        declared_seed="-1950884254474434485",
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        assert len(bridge.started) == 1
        assert engine.state["status"] == "paused"
        events = run_events(engine)
        decision = next(event["data"] for event in events if event["type"] == "skill_decision")
        assert decision["validation_provider"] == "local"
        assert "Jev unavailable" in decision["rationale"]
        jev = [
            event["data"]
            for event in events
            if event["type"] == "inference" and event["data"]["provider"] == "jev"
        ]
        assert [event["phase"] for event in jev] == ["thinking", "failed"]
        assert "accepted" not in jev[-1].get("rationale", "")
        assert any(event["type"] == "strategy_fallback" for event in events)
    finally:
        await engine.close()


@pytest.mark.parametrize("later_jev_outage", [False, True])
async def test_holo_error_after_explicit_veto_cannot_execute_local_fallback(
    tmp_path, later_jev_outage
):
    class VetoThenHoloFailure(FirstCandidateStrategist):
        async def choose(self, observation, state, candidates, context, provider="holo"):
            result = await super().choose(observation, state, candidates, context, provider)
            if provider == "holo" and len(self.calls) > 1:
                raise ProviderError("Malformed Holo output", provider="holo")
            if provider == "jev":
                error_type = (
                    ProviderError if later_jev_outage and len(self.calls) > 2 else ReplanRequired
                )
                raise error_type("Jev rejected or became unavailable", provider="jev")
            return result

    engine, bridge, strategist = await engine_with_fakes(
        tmp_path,
        PortalBridge(),
        strategist=VetoThenHoloFailure(),
        jev_api_key="unused-test-jev",
        run_goal="nether",
        nether_route="ruined_portal",
        declared_seed="-1950884254474434485",
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        assert bridge.started == []
        assert engine.state["status"] == "error"
        assert [call["provider"] for call in strategist.calls] == ["holo", "jev"] * 8
        validation = strategist.calls[3]["context"]
        assert validation["proposal_origin"] == "local"
        assert "previous_local_proposal" in validation
        assert "previous_holo_plan" not in validation
        events = run_events(engine)
        assert len([event for event in events if event["type"] == "strategy_rejected"]) == 8
        assert not any(event["type"] == "skill_started" for event in events)
    finally:
        await engine.close()


async def test_new_actual_jev_approval_can_release_previously_vetoed_local_fallback(tmp_path):
    class VetoThenApprove(FirstCandidateStrategist):
        async def choose(self, observation, state, candidates, context, provider="holo"):
            result = await super().choose(observation, state, candidates, context, provider)
            if provider == "holo" and len(self.calls) > 1:
                raise ProviderError("Malformed Holo output", provider="holo")
            if provider == "jev" and len(self.calls) == 2:
                raise ReplanRequired("First proposal vetoed", provider="jev")
            return result

    engine, bridge, strategist = await engine_with_fakes(
        tmp_path,
        PortalBridge(),
        strategist=VetoThenApprove(),
        jev_api_key="unused-test-jev",
        run_goal="nether",
        nether_route="ruined_portal",
        declared_seed="-1950884254474434485",
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        assert [call["provider"] for call in strategist.calls] == ["holo", "jev", "holo", "jev"]
        assert len(bridge.started) == 1
        decision = next(
            event["data"] for event in run_events(engine) if event["type"] == "skill_decision"
        )
        assert decision["provider"] == "local"
        assert decision["validation_provider"] == "jev"
    finally:
        await engine.close()


class WaitingJev(FirstCandidateStrategist):
    def __init__(self, suppress):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.suppress = suppress

    async def choose(self, observation, state, candidates, context, provider="holo"):
        if provider == "jev":
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.suppress:
                    raise
        return await super().choose(observation, state, candidates, context, provider)


@pytest.mark.parametrize("suppress", [False, True])
async def test_pause_during_jev_validation_marks_cancelled_and_never_starts_late_skill(
    tmp_path, suppress
):
    strategist = WaitingJev(suppress)
    engine, bridge, _ = await engine_with_fakes(
        tmp_path, strategist=strategist, jev_api_key="unused-test-jev"
    )
    try:
        await engine.start()
        await asyncio.wait_for(strategist.entered.wait(), 2)
        live = [event["data"] for event in engine.events if event["type"] == "inference"]
        assert live[-1]["provider"] == "jev" and live[-1]["phase"] == "thinking"
        await asyncio.wait_for(engine.pause("Pause during Jev validation"), 2)
        assert bridge.started == []
        assert engine.state["status"] == "paused"
        jev = [
            event["data"]
            for event in run_events(engine)
            if event["type"] == "inference" and event["data"]["provider"] == "jev"
        ]
        assert [event["phase"] for event in jev] == ["thinking", "failed"]
        assert jev[-1]["cancelled"] is True
        assert jev[0]["request_id"] == jev[-1]["request_id"]
    finally:
        strategist.release.set()
        await engine.close()


async def test_state_is_revalidated_again_after_jev_changes_the_inventory(tmp_path):
    bridge = SkillBridge()

    class ChangingJev(FirstCandidateStrategist):
        async def choose(self, observation, state, candidates, context, provider="holo"):
            result = await super().choose(observation, state, candidates, context, provider)
            if provider == "jev":
                bridge.inventory = {"minecraft:stone_pickaxe": 1, "minecraft:stone_axe": 1}
            return result

    engine, _, _ = await engine_with_fakes(
        tmp_path, bridge, ChangingJev(), jev_api_key="unused-test-jev"
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        assert bridge.started == []
        assert engine.state["status"] == "paused"
        assert engine.state["objective"] == "Stone tools acquired"
        assert any(
            event["type"] == "event" and event["data"].get("name") == "stale_skill_discarded"
            for event in run_events(engine)
        )
    finally:
        await engine.close()


async def test_pause_after_validation_during_final_hud_never_dispatches_skill(tmp_path):
    class PausingBridge(SkillBridge):
        engine = None
        did_pause = False

        async def send(self, message):
            await super().send(message)
            if self.engine and self.engine.state["action"] is not None and not self.did_pause:
                self.did_pause = True
                await self.engine.pause("Pause before skill dispatch")

    bridge = PausingBridge()
    engine, _, _ = await engine_with_fakes(tmp_path, bridge, jev_api_key="unused-test-jev")
    bridge.engine = engine
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.gather(engine.task, return_exceptions=True), 5)
        assert bridge.did_pause
        assert bridge.started == []
        assert engine.state["status"] == "paused"
        assert not any(event["type"] == "skill_started" for event in run_events(engine))
    finally:
        await engine.close()
