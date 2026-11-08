"""State-agent orchestration against real prerequisite and inventory accounting."""

import asyncio
import copy
import gzip
import hashlib
import json
import time

import pytest
from holospeed.bridge import BridgeError
from holospeed.config import Settings
from holospeed.engine import AgentEngine
from holospeed.game_state import GameState
from holospeed.recording import read_events
from holospeed.strategy import StrategySelection
from test_runtime import frame
from test_skills import apply_inventory_postcondition, cell, fixture, set_inventory


class SkillBridge:
    """A client peer with explicit postcondition updates, separate from its ACKs."""

    connected = True

    def __init__(self, *, behavior="complete"):
        self.value = fixture()
        self.inventory = {}
        self.behavior = behavior
        self.started = []
        self.stops = []
        self.messages = []
        self.state_reads = 0
        self.observations = 0
        self.pending = None
        self.pending_reads = 0
        self.skill = self.idle_skill()
        self.skill_started = asyncio.Event()
        self.journal = None

    @staticmethod
    def idle_skill():
        return {
            "baritone_available": True,
            "active": False,
            "skill_id": None,
            "kind": None,
            "item": None,
            "target_count": 0,
            "inventory_count": 0,
            "status": "idle",
            "reason": None,
            "phase": "idle",
            "elapsed_ticks": 0,
            "max_ticks": 0,
            "keys": [],
        }

    def _complete_inventory(self):
        candidate = self.pending
        if (
            candidate["skill"] == "craft"
            and candidate["item"]
            in {"minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:stone_axe"}
            and self.inventory.get("minecraft:crafting_table", 0)
        ):
            self.inventory["minecraft:crafting_table"] -= 1
            placed = cell(1, 64, 0, "crafting_table", True)
            self.value["local_blocks"] = [
                placed if block["position"] == placed["position"] else block
                for block in self.value["local_blocks"]
            ]
            self.value["nearby_blocks"].append(placed)
        apply_inventory_postcondition(self.inventory, candidate)
        self.skill.update(
            active=False,
            status="completed",
            phase="done",
            keys=[],
            inventory_count=self.inventory[candidate["item"]],
            reason="Actual inventory postcondition satisfied",
        )
        self.pending = None

    async def game_state(self):
        self.state_reads += 1
        if self.pending:
            self.pending_reads += 1
            self.skill["elapsed_ticks"] = min(self.pending_reads, 1800)
            if self.behavior == "complete" and self.pending_reads >= 2:
                self._complete_inventory()
        set_inventory(self.value, self.inventory)
        self.value.update(
            id=f"state-{self.state_reads}",
            tick=self.state_reads,
            sampled_at_ms=int(time.time() * 1000),
            cache_age_ms=0,
            skill=copy.deepcopy(self.skill),
        )
        return GameState.model_validate(self.value)

    async def observe(self):
        self.observations += 1
        return frame(f"frame-{self.observations}")

    async def start_skill(self, candidate):
        self.started.append(copy.deepcopy(candidate))
        skill_id = f"skill-{len(self.started)}"
        self.skill = {
            "baritone_available": True,
            "active": True,
            "skill_id": skill_id,
            "kind": candidate["skill"],
            "item": candidate["item"],
            "target_count": candidate["target_count"],
            "inventory_count": self.inventory.get(candidate["item"], 0),
            "status": "running",
            "reason": None,
            "phase": "working",
            "elapsed_ticks": 0,
            "max_ticks": candidate["max_ticks"],
            "keys": ["forward", "attack"] if candidate["skill"] == "mine" else [],
        }
        result = "accepted"
        if self.behavior == "false_completion":
            # Both the ACK and its convenience count lie. Only actual inventory
            # is valid completion evidence, and this fake never updates it.
            self.skill.update(
                active=False,
                status="completed",
                phase="done",
                keys=[],
                inventory_count=candidate["target_count"],
                reason="Claimed completion",
            )
            result = "completed"
        elif self.behavior == "rejected":
            self.skill.update(active=False, status="failed", phase="idle", keys=[])
            result = "rejected"
        else:
            self.pending = copy.deepcopy(candidate)
            self.pending_reads = 0
        self.skill_started.set()
        return {
            "type": "skill_ack",
            "id": f"request-{len(self.started)}",
            "status": result,
            "skill_id": skill_id,
            "reason": self.skill["reason"],
            "skill": copy.deepcopy(self.skill),
        }

    async def stop(self, reason):
        self.stops.append(reason)
        self.pending = None
        if self.skill["active"]:
            self.skill.update(active=False, status="cancelled", phase="cancelled", keys=[])

    async def send(self, message):
        self.messages.append(copy.deepcopy(message))


class FirstCandidateStrategist:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def choose(self, observation, state, candidates, context, provider="holo"):
        self.calls.append(
            {
                "provider": provider,
                "state": copy.deepcopy(state),
                "candidates": copy.deepcopy(candidates),
                "context": copy.deepcopy(context),
            }
        )
        return StrategySelection(
            candidate_id=candidates[0]["id"],
            rationale="Choose the verified next prerequisite",
            provider=provider,
            latency_ms=2.0,
            usage={"input_tokens": 10, "output_tokens": 3},
            request={"candidate_ids": [item["id"] for item in candidates]},
            response={"candidate_id": candidates[0]["id"]},
            model="fake-strategist",
        )

    async def aclose(self):
        self.closed = True


class WaitingStrategist(FirstCandidateStrategist):
    def __init__(self, *, suppress_cancellation=False):
        super().__init__()
        self.suppress_cancellation = suppress_cancellation
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def choose(self, observation, state, candidates, context, provider="holo"):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            if not self.suppress_cancellation:
                raise
        return await super().choose(observation, state, candidates, context, provider)


async def engine_with_fakes(tmp_path, bridge=None, strategist=None, **settings):
    bridge = bridge or SkillBridge()
    strategist = strategist or FirstCandidateStrategist()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="state",
            runs_dir=tmp_path,
            holo_api_key="unused-test-key",
            **settings,
        ),
        bridge,
    )
    await engine.strategist.aclose()
    engine.strategist = strategist
    return engine, bridge, strategist


def run_events(engine):
    return read_events(engine.recorder.path / "events.jsonl")


async def test_state_run_completes_eight_inventory_verified_skills_and_preserves_journal(tmp_path):
    engine, bridge, strategist = await engine_with_fakes(tmp_path)
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=15)
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 8
        assert engine.state["metrics"]["errors"] == 0
        assert [item["item"] for item in bridge.started] == [
            "minecraft:oak_log",
            "minecraft:oak_planks",
            "minecraft:crafting_table",
            "minecraft:stick",
            "minecraft:wooden_pickaxe",
            "minecraft:cobblestone",
            "minecraft:stone_pickaxe",
            "minecraft:stone_axe",
        ]
        assert [item["target_count"] for item in bridge.started] == [3, 12, 1, 8, 1, 6, 1, 1]
        assert bridge.inventory["minecraft:crafting_table"] == 0  # Placed before 3x3 recipes.
        assert bridge.inventory["minecraft:stone_pickaxe"] == 1
        assert bridge.inventory["minecraft:stone_axe"] == 1
        assert [item["id"] for item in engine.state["milestones"]] == [
            "wood",
            "crafting_table",
            "wooden_pickaxe",
            "stone_tools",
        ]
        assert {item["verification"] for item in engine.state["milestones"]} == {"game_state"}
        assert strategist.calls and strategist.calls[0]["provider"] == "holo"
        assert bridge.observations >= 8
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert bridge.stops
        events = run_events(engine)
        assert len([event for event in events if event["type"] == "skill_started"]) == 8
        assert len([event for event in events if event["type"] == "skill_completed"]) == 8
        assert not any(event["type"] == "skill_failed" for event in events)
        assert any(
            event["type"] == "event" and event["data"].get("name") == "goal_reached"
            for event in events
        )
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        snapshots = [event["data"] for event in events if event["type"] == "game_state"]
        assert len(snapshots) == bridge.state_reads
        for snapshot in snapshots:
            raw = gzip.decompress((engine.recorder.path / snapshot["path"]).read_bytes())
            assert hashlib.sha256(raw).hexdigest() == snapshot["sha256"]
            saved = GameState.model_validate_json(raw)
            assert saved.id == snapshot["id"]
            assert saved.tick == snapshot["tick"]
            assert len(saved.local_blocks) == 125
        state_ids = {snapshot["id"] for snapshot in snapshots}
        assert all(item["state_id"] in state_ids for item in engine.state["milestones"])
        summary = json.loads((engine.recorder.path / "summary.json").read_text())
        assert summary["metrics"]["actions"] == 8
        assert summary["milestones"] == engine.state["milestones"]
    finally:
        await engine.close()


@pytest.mark.parametrize("behavior", ["false_completion", "rejected"])
async def test_unverified_ack_does_not_earn_actions_or_milestones_or_repeat_forever(
    tmp_path, behavior
):
    engine, bridge, _ = await engine_with_fakes(tmp_path, SkillBridge(behavior=behavior))
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=5)
        assert engine.state["status"] == "error"
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["milestones"] == []
        assert len(bridge.started) == 1  # Same candidate cannot loop against unchanged inventory.
        assert bridge.inventory == {}
        events = run_events(engine)
        assert any(event["type"] == "skill_failed" for event in events)
        assert not any(event["type"] in {"skill_completed", "milestone"} for event in events)
    finally:
        await engine.close()


@pytest.mark.parametrize("suppress_cancellation", [False, True])
async def test_state_pause_during_strategy_never_starts_a_late_skill(
    tmp_path, suppress_cancellation
):
    strategist = WaitingStrategist(suppress_cancellation=suppress_cancellation)
    engine, bridge, _ = await engine_with_fakes(tmp_path, strategist=strategist)
    try:
        await engine.start()
        await asyncio.wait_for(strategist.entered.wait(), timeout=2)
        await asyncio.wait_for(engine.pause("Pause during strategy selection"), timeout=2)
        strategist.release.set()
        assert strategist.cancelled.is_set()
        assert engine.task.done()
        assert bridge.started == []
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert "Pause during strategy selection" in bridge.stops
        assert not any(event["type"] == "skill_started" for event in run_events(engine))
    finally:
        strategist.release.set()
        await engine.close()


@pytest.mark.parametrize("suppress_cancellation", [False, True])
async def test_state_run_deadline_discards_late_strategy_result(tmp_path, suppress_cancellation):
    strategist = WaitingStrategist(suppress_cancellation=suppress_cancellation)
    engine, bridge, _ = await engine_with_fakes(
        tmp_path,
        strategist=strategist,
        max_run_seconds=0.1,
    )
    try:
        await engine.start()
        await asyncio.wait_for(strategist.entered.wait(), timeout=1)
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=2)
        assert strategist.cancelled.is_set()
        assert bridge.started == []
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["metrics"]["errors"] == 0
        assert bridge.stops
        assert any(
            event["type"] == "event" and event["data"].get("name") == "run_budget_reached"
            for event in run_events(engine)
        )
    finally:
        strategist.release.set()
        await engine.close()


async def test_state_run_deadline_stops_active_mining_without_claiming_success(tmp_path):
    engine, bridge, _ = await engine_with_fakes(
        tmp_path,
        SkillBridge(behavior="never_complete"),
        max_run_seconds=0.3,
    )
    try:
        await engine.start()
        await asyncio.wait_for(bridge.skill_started.wait(), timeout=1)
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=2)
        assert len(bridge.started) == 1
        assert bridge.pending is None
        assert not bridge.skill["active"]
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["milestones"] == []
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert not any(event["type"] == "skill_completed" for event in run_events(engine))
        assert any(
            event["type"] == "event" and event["data"].get("name") == "run_budget_reached"
            for event in run_events(engine)
        )
    finally:
        await engine.close()


async def test_skill_wall_deadline_stops_a_client_that_never_reports_completion(
    tmp_path, monkeypatch
):
    from holospeed import state_engine

    real_planner = state_engine.next_skills

    def shortest_allowed_budget(state, completed_milestones=(), **options):
        return [
            {**candidate, "max_ticks": 20}
            for candidate in real_planner(state, completed_milestones, **options)
        ]

    monkeypatch.setattr(state_engine, "next_skills", shortest_allowed_budget)
    engine, bridge, _ = await engine_with_fakes(
        tmp_path,
        SkillBridge(behavior="never_complete"),
        max_run_seconds=10,
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=8)
        assert len(bridge.started) == 1
        assert engine.state["status"] == "error"
        assert engine.state["metrics"]["actions"] == 0
        assert not bridge.skill["active"]
        assert "Skill wall-clock deadline reached" in bridge.stops
        failures = [event for event in run_events(engine) if event["type"] == "skill_failed"]
        assert len(failures) == 1
        assert failures[0]["data"]["reason"] == "Skill wall-clock deadline reached"
        assert not any(event["type"] == "skill_completed" for event in run_events(engine))
    finally:
        await engine.close()


class PortalBridge(SkillBridge):
    """A normal portal attempt with explicit dimension transfer and load gaps."""

    def __init__(self, *, behavior="complete", stale_polls=0, failure=None):
        super().__init__(behavior=behavior)
        self.stale_polls = stale_polls
        self.failed_polls = 0
        self.failure = failure

    def _complete_inventory(self):
        assert self.pending["skill"] == "portal"
        self.value["dimension"] = "minecraft:the_nether"
        self.skill.update(active=False, status="completed", phase="entered_nether", keys=[])
        self.pending = None

    async def game_state(self):
        self.skill["capabilities"] = ["portal:minecraft:nether_portal"]
        if self.pending:
            if self.failure:
                self.failed_polls += 1
                raise BridgeError(self.failure)
            if self.stale_polls:
                self.stale_polls -= 1
                self.failed_polls += 1
                raise BridgeError("Structured game state is stale")
        return await super().game_state()


@pytest.mark.parametrize("stale_polls", [0, 2])
async def test_portal_goal_completes_only_after_ready_nether_snapshot(tmp_path, stale_polls):
    bridge = PortalBridge(stale_polls=stale_polls)
    engine, _, strategist = await engine_with_fakes(
        tmp_path, bridge, run_goal="nether", declared_seed="-1950884254474434485"
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=5)
        assert engine.state["status"] == "paused"
        assert engine.state["objective"] == "Nether reached"
        assert engine.state["metrics"]["actions"] == 1
        assert engine.state["metrics"]["errors"] == 0
        assert len(bridge.started) == 1
        assert bridge.started[0]["skill"] == "portal"
        assert bridge.failed_polls == stale_polls
        assert bridge.inventory == {}
        assert "Nether" in strategist.calls[0]["context"]["run_goal"]
        assert [item["id"] for item in engine.state["milestones"]] == ["nether"]
        assert engine.state["milestones"][0]["evidence"] == {
            "inventory": {},
            "dimension": "minecraft:the_nether",
            "verified_steps": [],
        }
        events = run_events(engine)
        completion = next(event["data"] for event in events if event["type"] == "skill_completed")
        assert completion["postcondition"] == "dimension"
        assert completion["dimension"] == completion["expected_dimension"] == "minecraft:the_nether"
        state = next(
            event["data"]
            for event in events
            if event["type"] == "game_state" and event["data"]["id"] == completion["state_id"]
        )
        assert state["dimension"] == "minecraft:the_nether"
        assert state["world_ready"] is True
        assert 0 <= state["cache_age_ms"] <= 1000
        assert "Nether entry verified; release portal controls" in bridge.stops
    finally:
        await engine.close()


async def test_completed_portal_ack_in_overworld_never_earns_goal_or_action(tmp_path):
    bridge = PortalBridge(behavior="false_completion")
    engine, _, _ = await engine_with_fakes(
        tmp_path, bridge, run_goal="nether", declared_seed="-1950884254474434485"
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=5)
        assert engine.state["status"] == "error"
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["milestones"] == []
        assert len(bridge.started) == 1
        assert bridge.value["dimension"] == "minecraft:overworld"
        events = run_events(engine)
        assert any(event["type"] == "skill_failed" for event in events)
        assert not any(event["type"] in {"skill_completed", "milestone"} for event in events)
    finally:
        await engine.close()


async def test_transition_grace_cannot_extend_global_run_deadline(tmp_path):
    bridge = PortalBridge(stale_polls=100)
    engine, _, _ = await engine_with_fakes(
        tmp_path,
        bridge,
        run_goal="nether",
        declared_seed="-1950884254474434485",
        max_run_seconds=0.3,
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=2)
        assert bridge.failed_polls >= 1
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 0
        assert not bridge.skill["active"]
        assert any(
            event["type"] == "event" and event["data"].get("name") == "run_budget_reached"
            for event in run_events(engine)
        )
    finally:
        await engine.close()


async def test_portal_transfer_does_not_retry_unrelated_bridge_failure(tmp_path):
    bridge = PortalBridge(failure="Minecraft disconnected")
    engine, _, _ = await engine_with_fakes(
        tmp_path, bridge, run_goal="nether", declared_seed="-1950884254474434485"
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=2)
        assert bridge.failed_polls == 1
        assert engine.state["status"] == "error"
        assert engine.state["metrics"]["actions"] == 0
        assert not bridge.skill["active"]
        assert not any(event["type"] == "skill_completed" for event in run_events(engine))
    finally:
        await engine.close()
