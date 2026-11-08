"""Exercise state and skill RPCs through the real bridge request lifecycle."""

import copy
import time

import pytest
from holospeed.bridge import BridgeError, GameBridge
from holospeed.game_state import GameState
from pydantic import ValidationError
from test_skills import fixture


class ReplySocket:
    def __init__(self, bridge, reply):
        self.bridge = bridge
        self.reply = reply
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, **kwargs):
        pass

    async def send_json(self, packet):
        self.sent.append(copy.deepcopy(packet))
        response = {**copy.deepcopy(self.reply), "id": packet["id"]}
        await self.bridge.receive(response)


async def connected(reply):
    bridge = GameBridge()
    socket = ReplySocket(bridge, reply)
    assert await bridge.attach(socket)
    await bridge.receive({"type": "hello", "protocol": 1, "minecraft": "1.21.1"})
    return bridge, socket


def state_reply(**changes):
    value = fixture()
    value.update(sampled_at_ms=999_500, cache_age_ms=0, **changes)
    return value


def skill_candidate(**changes):
    return {
        "id": "mine-minecraft-oak_log-3",
        "skill": "mine",
        "item": "minecraft:oak_log",
        "target_count": 3,
        "max_ticks": 1200,
        "reason": "Gather recipe prerequisites",
        "nearest_target": {"position": {"x": 2, "y": 65, "z": 0}, "distance": 2.5},
        "risks": ["local_map_incomplete"],
        "inventory_count": 0,
        "path_validated": False,
        **changes,
    }


async def test_game_state_returns_validated_fresh_snapshot_without_consuming_visual_frame(
    monkeypatch,
):
    monkeypatch.setattr("holospeed.bridge.time.time", lambda: 1000.0)
    bridge, socket = await connected(state_reply())
    frames = {"existing-visual-frame": time.monotonic()}
    bridge.frames = frames.copy()
    journal = []
    bridge.journal = lambda kind, data: journal.append((kind, copy.deepcopy(data)))
    result = await bridge.game_state()
    assert isinstance(result, GameState)
    assert result.id == socket.sent[0]["id"]
    assert result.sampled_at_ms == 999_500
    assert socket.sent == [{"type": "state", "id": result.id}]
    assert bridge.frames == frames
    assert bridge.pending == {}
    assert journal == [("bridge_command", socket.sent[0])]
    await bridge.detach(socket)


@pytest.mark.parametrize(
    "changes",
    [
        {"sampled_at_ms": 998_999},
        {"sampled_at_ms": 1_000_001},
        {"sampled_at_ms": 1_000_000, "cache_age_ms": 1001},
        {"world_ready": False, "player": None, "dimension": None},
    ],
)
async def test_stale_future_cached_and_unavailable_states_are_rejected(monkeypatch, changes):
    monkeypatch.setattr("holospeed.bridge.time.time", lambda: 1000.0)
    value = state_reply()
    value.update(changes)
    bridge, socket = await connected(value)
    with pytest.raises(BridgeError, match="state is stale"):
        await bridge.game_state()
    assert bridge.pending == {}
    await bridge.detach(socket)


async def test_wrong_state_response_type_and_malformed_schema_fail_before_use(monkeypatch):
    monkeypatch.setattr("holospeed.bridge.time.time", lambda: 1000.0)
    bridge, socket = await connected({"type": "observation"})
    with pytest.raises(BridgeError, match="does not support structured"):
        await bridge.game_state()
    socket.reply = state_reply()
    socket.reply["player"]["health"] = "20"
    with pytest.raises(ValidationError):
        await bridge.game_state()
    assert bridge.pending == {}
    await bridge.detach(socket)


@pytest.mark.parametrize("status", ["accepted", "completed", "rejected"])
async def test_skill_rpc_strips_planning_metadata_and_preserves_ack_status(status):
    reply = {"type": "skill_ack", "status": status, "skill_id": "client-skill-1", "reason": None}
    bridge, socket = await connected(reply)
    journal = []
    bridge.journal = lambda kind, data: journal.append((kind, copy.deepcopy(data)))
    candidate = skill_candidate()
    original = copy.deepcopy(candidate)
    ack = await bridge.start_skill(candidate)
    assert candidate == original
    assert ack["status"] == status
    assert ack["skill_id"] == "client-skill-1"
    packet = socket.sent[0]
    assert set(packet) == {"type", "id", "skill", "item", "target_count", "max_ticks"}
    assert packet["type"] == "skill"
    assert packet["id"] != candidate["id"]
    assert packet["target_count"] == 3
    assert packet["max_ticks"] == 1200
    assert bridge.pending == {}
    assert [kind for kind, _ in journal] == ["bridge_command", "bridge_ack"]
    await bridge.detach(socket)


@pytest.mark.parametrize(
    "changes",
    [
        {"skill": "teleport"},
        {"skill": "give"},
        {"target_count": 0},
        {"target_count": 65},
        {"target_count": True},
        {"target_count": "3"},
        {"max_ticks": 0},
        {"max_ticks": 19},
        {"max_ticks": 1801},
        {"max_ticks": True},
        {"max_ticks": "20"},
    ],
)
async def test_unsupported_or_out_of_bounds_skill_is_rejected_without_sending(changes):
    bridge, socket = await connected({"type": "skill_ack", "status": "accepted"})
    with pytest.raises(BridgeError):
        await bridge.start_skill(skill_candidate(**changes))
    assert socket.sent == []
    assert bridge.pending == {}
    await bridge.detach(socket)


@pytest.mark.parametrize("max_ticks", [20, 1800])
async def test_skill_deadline_endpoints_match_fabric_bounds(max_ticks):
    bridge, socket = await connected({"type": "skill_ack", "status": "accepted"})
    assert (await bridge.start_skill(skill_candidate(max_ticks=max_ticks)))["status"] == "accepted"
    assert socket.sent[0]["max_ticks"] == max_ticks
    await bridge.detach(socket)


@pytest.mark.parametrize(
    "reply",
    [
        {"type": "ack", "status": "completed"},
        {"type": "skill_ack", "status": "running"},
        {"type": "skill_ack", "status": "cancelled"},
        {"type": "skill_ack"},
    ],
)
async def test_invalid_skill_ack_is_not_promoted_to_an_execution_result(reply):
    bridge, socket = await connected(reply)
    with pytest.raises(BridgeError, match="Invalid skill acknowledgment"):
        await bridge.start_skill(skill_candidate())
    assert bridge.pending == {}
    await bridge.detach(socket)


async def test_arm_uses_explicit_rpc_and_five_second_timeout(monkeypatch):
    bridge, socket = await connected({"type": "arm_ack", "status": "ready"})
    request = bridge.request
    deadlines = []

    async def observe_timeout(packet, timeout):
        deadlines.append(timeout)
        return await request(packet, timeout)

    monkeypatch.setattr(bridge, "request", observe_timeout)
    assert await bridge.arm() is None
    assert deadlines == [5]
    assert len(socket.sent) == 1
    assert socket.sent[0]["type"] == "arm"
    assert set(socket.sent[0]) == {"type", "id"}
    assert bridge.pending == {}
    await bridge.detach(socket)


@pytest.mark.parametrize(
    "reply",
    [
        {"type": "arm_ack", "status": "rejected"},
        {"type": "arm_ack", "status": "completed"},
        {"type": "arm_ack"},
        {"type": "skill_ack", "status": "ready"},
    ],
)
async def test_arm_rejects_every_ack_except_arm_ready(reply):
    bridge, socket = await connected(reply)
    with pytest.raises(BridgeError, match="Minecraft cannot be armed"):
        await bridge.arm()
    assert bridge.pending == {}
    await bridge.detach(socket)


async def test_arm_preserves_rejection_reason_for_operator_feedback():
    bridge, socket = await connected(
        {
            "type": "arm_ack",
            "status": "rejected",
            "reason": "Enter a survival world first",
        }
    )
    with pytest.raises(BridgeError, match="Enter a survival world first"):
        await bridge.arm()
    assert bridge.pending == {}
    await bridge.detach(socket)


def portal_candidate(**changes):
    return skill_candidate(
        skill="portal",
        item="minecraft:nether_portal",
        target_count=1,
        target={"x": 48, "y": 68, "z": 48},
        postcondition="dimension",
        expected_dimension="minecraft:the_nether",
        **changes,
    )


async def test_portal_rpc_preserves_bounded_landmark_and_strips_planner_metadata():
    bridge, socket = await connected({"type": "skill_ack", "status": "accepted", "skill_id": "p"})
    candidate = portal_candidate()
    original = copy.deepcopy(candidate)
    ack = await bridge.start_skill(candidate)
    assert ack["status"] == "accepted"
    packet = socket.sent[0]
    assert set(packet) == {"type", "id", "skill", "item", "target_count", "max_ticks", "target"}
    assert packet["target"] == {"x": 48, "y": 68, "z": 48}
    assert packet["skill"] == "portal" and packet["target_count"] == 1
    assert candidate == original
    assert bridge.pending == {}
    await bridge.detach(socket)


@pytest.mark.parametrize(
    "changes",
    [
        {"item": "minecraft:obsidian"},
        {"target_count": 2},
        {"target": None},
        {"target": {"x": 48, "z": 48}},
        {"target": {"x": 48, "y": 68, "z": 48, "dimension": "minecraft:the_nether"}},
        {"target": {"x": True, "y": 68, "z": 48}},
        {"target": {"x": 48.0, "y": 68, "z": 48}},
        {"target": {"x": 48, "y": -65, "z": 48}},
        {"target": {"x": 48, "y": 321, "z": 48}},
        {"target": {"x": 29_999_985, "y": 68, "z": 48}},
        {"target": {"x": 48, "y": 68, "z": -29_999_985}},
    ],
)
async def test_portal_rpc_rejects_invalid_objectives_and_landmarks_without_sending(changes):
    bridge, socket = await connected({"type": "skill_ack", "status": "accepted"})
    candidate = portal_candidate()
    candidate.update(changes)
    with pytest.raises(BridgeError):
        await bridge.start_skill(candidate)
    assert socket.sent == []
    assert bridge.pending == {}
    await bridge.detach(socket)


async def test_cast_rpc_reserves_construction_bounds_and_preserves_actual_lava_target():
    bridge, socket = await connected({"type": "skill_ack", "status": "accepted"})
    candidate = {**portal_candidate(), "skill": "cast_portal", "target": {"x": 4, "y": 63, "z": 2}}
    await bridge.start_skill(candidate)
    assert socket.sent[0]["skill"] == "cast_portal"
    assert socket.sent[0]["target"] == candidate["target"]
    for y in (-64, 314):
        with pytest.raises(BridgeError, match="construction needs room"):
            await bridge.start_skill({**candidate, "target": {"x": 4, "y": y, "z": 2}})
    assert len(socket.sent) == 1
    await bridge.detach(socket)


async def test_smelt_rpc_only_allows_the_implemented_iron_recipe():
    bridge, socket = await connected({"type": "skill_ack", "status": "accepted"})
    await bridge.start_skill(
        skill_candidate(skill="smelt", item="minecraft:iron_ingot", target_count=4)
    )
    with pytest.raises(BridgeError, match="Unsupported smelting"):
        await bridge.start_skill(skill_candidate(skill="smelt", item="minecraft:diamond"))
    assert len(socket.sent) == 1
    await bridge.detach(socket)
