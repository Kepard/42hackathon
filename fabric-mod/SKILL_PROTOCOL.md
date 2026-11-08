# Bounded local skills

Local skills use ordinary survival mechanics while Holo4 selects goals. They
share the authenticated loopback bridge and exclusively own inputs until they
finish or are cancelled. They do not run chat commands, spawn items, edit world
blocks, teleport, change game mode, or bypass normal mining/crafting rules.

```json
{
  "type": "skill",
  "id": "collect-opening-logs",
  "skill": "mine",
  "item": "minecraft:birch_log",
  "target_count": 3,
  "max_ticks": 1200
}
```

`skill` is `mine` or `craft` for inventory tasks; the later portal section
defines its separate dimension-verified task. `target_count` is the desired **total inventory
count**, not an additional quantity; its range is 1–64 and default is 1.
`max_ticks` ranges from 20–1800 and defaults to 1200. A wall-clock deadline of
`max_ticks * 50 ms + 2 seconds` also applies, including during low tick rates.
Unknown request fields, unsupported targets and noninteger limits are rejected.
A Minecraft registry namespace is optional and defaults to `minecraft`.

The immediate response is `skill_ack` with the request `id`, a `skill_id`,
`status`, optional `reason`, and a current `skill` snapshot. ACK statuses are:

* `accepted`: a bounded skill is running.
* `completed`: the inventory target was already satisfied.
* `rejected`: the request or its initial preconditions failed.

Poll the existing `state` request to inspect `game_state.skill` until
`active=false`. The skill status is `idle`, `running`, `completed`, `failed`, or
`cancelled`. Snapshot fields include `baritone_available`, `active`, `skill_id`,
`kind`, `item`, `target_count`, `inventory_count`, `status`, `reason`, `phase`,
`elapsed_ticks`, `max_ticks`, optional `known_table`, and optional `keys`. `keys` comes from vanilla
pressed bindings plus read-only Baritone forced-input queries. Null means
readback is unavailable; an empty list means no verified held keys. The HUD
labels active skill/phase and shows an explicit unavailable label when needed. `phase=cancellation_pending` retains exclusive
input ownership while a failed Baritone cleanup is retried; a terminal status
alone is not permission to issue another action while `active=true`.

Mining starts in `preparing_mining` while a daemon worker prepares Baritone’s
immutable block/drop lookup. The client never waits for an unfinished lookup:
only a later client tick starts mining. Cancellation discards the result, so a
late worker completion cannot resume a stopped skill.

Mining uses the optional public API from **Baritone Fabric 1.11.3**, which
supports Minecraft 1.21.1. Install the official
[`baritone-api-fabric-1.11.3.jar`](https://github.com/cabaletta/baritone/releases/download/v1.11.3/baritone-api-fabric-1.11.3.jar)
next to HoloSpeed in the instance's `mods` folder. The bridge has no compile-time
Baritone dependency; missing Baritone leaves visual controls and state reads
available. The published API jar's SHA-256 is
`f9a9b17a41d7d7ad22c759058b14fd01e3319663282e66f99e18153237fa6767`.

Mining targets are the eight overworld tree log types, stone, cobblestone,
deepslate, and cobbled deepslate. Stone/deepslate targets count their normal
cobblestone/cobbled-deepslate drops and require a suitable pickaxe. Completion
requires the inventory target; proximity or elapsed ticks alone do not count.
A mining skill fails after 400 ticks without inventory or position progress.

Crafting targets are overworld planks, sticks, a crafting table, a wooden
pickaxe, a stone pickaxe, and a stone axe. Crafting uses unlocked vanilla recipes,
normal recipe-fill requests and screen-handler slot clicks. It checks recipe
output, ingredients, an empty cursor, and an empty crafting grid, then waits for
normal inventory synchronization before reporting the result. It never writes
inventory stacks directly. Full inventory and missing ingredients fail clearly.
For a three-by-three recipe, the skill opens a reachable placed table or equips
an owned table through normal inventory SWAP, places it on a reachable solid
floor with an unobstructed top-face interaction, confirms the resulting client
block, then opens it. When the table was left behind and none is carried, the
skill can return to a remembered table using a bounded Baritone `GoalNear` path
before opening it. A remembered position is not proof of a valid route. Placement excludes the player, entities, fluids, and nearby
hazards. The previously selected hotbar slot is restored afterward.

The bridge rejects raw actions while a skill owns inputs and rejects skill
starts while an action or observation capture is pending. F8, controller stop,
disconnect, world/player change, death, focus loss, pause, and deadlines cancel
local skills. Baritone cancellation requests process cancellation, force-cancel,
and clear all overridden keys. F9 rearms and requests a fresh controller start.

`known_table` is null or an integer `{x,y,z}` position, remembered only after a
loaded client block was verified as a crafting table. Memory survives skill
boundaries and clears when the client-world identity changes. A loaded position
that no longer contains a table is forgotten; an unloaded position remains a
memory and is never inserted into current nearby block observations.

After restart, a player carrying a wooden or stone pickaxe can rediscover a
table in loaded chunks within ±8 blocks horizontally and ±24 vertically. This
read-only scan examines at most 14,161 cells and runs only without a known table,
at most once per 200 client ticks unless the player moves eight blocks. It can
run while idle, allowing the controller to plan a return before starting a
crafting skill. It never loads chunks. The same request deadline bounds return
navigation and crafting together; a return-capable request normally uses 1200
ticks. A 400-tick movement stall also terminates navigation. During table return,
cobblestone, cobbled deepslate, and blackstone are temporarily excluded from
Baritone's acceptable throwaway items so path placement does not consume tool
ingredients. The original setting is restored on success, failure, or stop.
The original list is never modified in place. If the remaining materials cannot
support a valid route, the bounded skill fails rather than fabricating tools.

These interfaces have compile/unit-test coverage. Successful normal mining,
crafting, automatic table placement, and cancellation still require runtime
validation using the updated game instance; see [verification](VERIFICATION.md).

## Operator start and resume

The dashboard's explicit Start action may send `{"type":"arm","id":"operator-1"}`.
The response is `arm_ack` with the same ID, `status:"ready"|"rejected"`, and an
optional reason. This operator request releases current controls and invalidates
old visual frames before it can clear the F8 latch. It requires a live player,
a loaded world, and a focused Minecraft window.

It may close the normal pause menu or an empty inventory/crafting screen through
vanilla close methods. It rejects unrelated screens and any crafting grid or
cursor containing items. If pause state has not updated yet, the bridge waits
across normal render/tick callbacks for up to two seconds; it never blocks the
render thread. It also waits for local skill input ownership to be released.
F8, controller stop, disconnect, or another arm request cancels a pending arm.
Only a successful ready acknowledgment clears the latch. F9 retains its existing
local start behavior. This protocol does not expose time, commands, or world edits.

## Verified ruined-portal route

The `portal:minecraft:nether_portal` capability enables a bounded composite task
for the explicitly selected 1.21.1 seed `-1950884254474434485`:

```json
{
  "type": "skill",
  "id": "enter-nether",
  "skill": "portal",
  "item": "minecraft:nether_portal",
  "target_count": 1,
  "max_ticks": 1800,
  "target": {"x": 48, "y": 68, "z": 48}
}
```

Only this verified landmark is accepted. The task checks loaded frame geometry,
approaches the real chest at (49,68,52), transfers owned supplies through normal
container clicks, repairs the missing obsidian at (50,72,50) by clicking the gold
support's bottom face, ignites the base's top face, and physically enters the
portal. It does not assume chest loot. The optional crying-obsidian corner is
left in place. Missing resources, changed geometry, obstructed interactions,
failed navigation, and timeouts stop the task clearly.

Normal Baritone goals handle travel. Portal entry uses the exact interior cell,
with at most twelve ticks of normal forward/jump input to finish a close step
into the portal plane. Inputs release while the player dwells inside and during
dimension loading. The expected world transition is tolerated only in entry
phases on the same network connection, with a five-second loading grace. The
only success condition is the actual `minecraft:the_nether` dimension; neither
portal construction nor an inventory count proves entry.

`skill.capabilities` lists implemented `kind:namespace:target` pairs. The optional
`skill.verified_steps` list records these persistent client-verified checkpoints:

| Step | Required evidence |
| --- | --- |
| `portal_located` | Loaded required frame and interior geometry match the verified route. |
| `chest_looted` | Normal transfer followed by a reopened chest and a fresh, applied server inventory packet confirms increased owned supplies. |
| `obsidian_acquired` | Obsidian is present in the player's inventory. |
| `ignition_acquired` | A fire charge or flint and steel is present in inventory. |
| `portal_repaired` | Every required frame block is obsidian; optional corners are ignored. |
| `portal_lit` | All nine interior cells actually contain Nether portal blocks. |

Checkpoint flags survive material consumption and terminal skill states for the
same route connection. They are not inferred from phase names and do not carry
into unrelated worlds. The controller separately confirms the Nether dimension.
The nearby-state scan prioritizes portal blocks, chests, furnaces and relevant
ore blocks within its existing 512-record bound.

A reusable `FurnaceController` is also present and unit-tested, but it is not
exposed or advertised by the bridge yet. This route does not currently include a
lava-casting fallback if the chest lacks the needed supplies.
