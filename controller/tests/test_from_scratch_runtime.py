"""Guard construction provenance, server-confirmed smelting, and post-goal survival."""

import asyncio

import pytest
from holospeed.bridge import BridgeError
from holospeed.recording import RunRecorder
from holospeed.state_engine import execute_skill
from test_state_runtime import PortalBridge, SkillBridge, engine_with_fakes, run_events


@pytest.mark.parametrize("status", ["failed", "cancelled", "completed"])
async def test_predicted_smelting_inventory_needs_completed_server_verification(tmp_path, status):
    class SmeltBridge(SkillBridge):
        async def start_skill(self, candidate):
            ack = await super().start_skill(candidate)
            self.pending = None
            self.inventory = {"minecraft:iron_ingot": 4}
            self.skill.update(
                active=False, status=status, phase="verifying_output", inventory_count=4
            )
            return ack

    engine, _, _ = await engine_with_fakes(tmp_path, SmeltBridge())
    engine.recorder = RunRecorder(tmp_path, "live", {})
    engine.state["status"] = "running"
    candidate = {
        "id": "smelt-four",
        "skill": "smelt",
        "item": "minecraft:iron_ingot",
        "target_count": 4,
        "max_ticks": 1200,
    }
    try:
        complete, _, _ = await execute_skill(engine, candidate, engine.generation)
        assert complete is (status == "completed")
    finally:
        await engine.close()


@pytest.mark.parametrize("verified", [False, True])
async def test_cast_transfer_requires_its_own_full_frame_and_ignition_proof(tmp_path, verified):
    class CastBridge(SkillBridge):
        async def start_skill(self, candidate):
            ack = await super().start_skill(candidate)
            self.pending = None
            self.value["dimension"] = "minecraft:the_nether"
            self.skill.update(
                active=False,
                status="completed",
                phase="entered_nether",
                verified_steps=["portal_built", "portal_lit"] if verified else [],
            )
            return ack

    engine, _, _ = await engine_with_fakes(tmp_path, CastBridge())
    engine.recorder = RunRecorder(tmp_path, "live", {})
    engine.state["status"] = "running"
    candidate = {
        "id": "cast-full-frame",
        "skill": "cast_portal",
        "item": "minecraft:nether_portal",
        "target_count": 1,
        "max_ticks": 1800,
        "target": {"x": 4, "y": 63, "z": 2},
    }
    try:
        complete, _, _ = await execute_skill(engine, candidate, engine.generation)
        assert complete is verified
    finally:
        await engine.close()


@pytest.mark.parametrize("available", [True, False])
async def test_world_pauses_after_goal_evidence_without_changing_achievement(tmp_path, available):
    class FreezingBridge(PortalBridge):
        pauses = 0

        async def pause_game(self):
            self.pauses += 1
            assert self.observations >= 2
            assert any(event["data"].get("name") == "goal_reached" for event in run_events(engine))
            if not available:
                raise BridgeError("Singleplayer pause unavailable")
            return True

    engine, bridge, _ = await engine_with_fakes(
        tmp_path,
        FreezingBridge(),
        run_goal="nether",
        nether_route="ruined_portal",
        declared_seed="-1950884254474434485",
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        assert engine.state["objective"] == "Nether reached"
        assert engine.state["status"] == "paused"
        assert bridge.pauses == 1
        names = [event["data"].get("name") for event in run_events(engine)]
        assert (
            "world_paused_after_goal" in names if available else "world_pause_unavailable" in names
        )
    finally:
        await engine.close()


@pytest.mark.parametrize("verified", [False, True])
async def test_failed_cast_provenance_cannot_become_success_on_next_loop(
    tmp_path, monkeypatch, verified
):
    class CastBridge(SkillBridge):
        async def start_skill(self, candidate):
            ack = await super().start_skill(candidate)
            self.pending = None
            self.value["dimension"] = "minecraft:the_nether"
            self.skill.update(
                active=False,
                status="completed",
                phase="entered_nether",
                verified_steps=["portal_built", "portal_lit"] if verified else [],
            )
            return ack

    candidate = {
        "id": "cast-full-frame",
        "skill": "cast_portal",
        "item": "minecraft:nether_portal",
        "target_count": 1,
        "max_ticks": 1800,
        "target": {"x": 4, "y": 63, "z": 2},
        "reason": "Construct the complete portal",
        "postcondition": "dimension",
        "expected_dimension": "minecraft:the_nether",
    }
    monkeypatch.setattr("holospeed.state_engine.next_skills", lambda *args, **kwargs: [candidate])
    engine, _, _ = await engine_with_fakes(tmp_path, CastBridge(), run_goal="nether")
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), 5)
        goals = [
            event for event in run_events(engine) if event["data"].get("name") == "goal_reached"
        ]
        assert bool(goals) is verified
        assert engine.state["metrics"]["actions"] == int(verified)
        assert engine.state["status"] == ("paused" if verified else "error")
    finally:
        await engine.close()
