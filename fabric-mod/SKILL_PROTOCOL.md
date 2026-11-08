# Bounded local skills

Local skills use ordinary survival mechanics while Holo4 selects goals. They
share the authenticated loopback bridge and exclusively own inputs until they
finish or are cancelled. They do not run chat commands, spawn items, write world
state directly, teleport, change game mode, or bypass normal mining/crafting rules.

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

`skill` is `mine`, `craft`, or `smelt` for inventory tasks; the later portal section
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
`elapsed_ticks`, `max_ticks`, optional `known_table`, `known_furnace`, and optional `keys`. `keys` comes from vanilla
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
deepslate, cobbled deepslate, raw iron, coal, flint, and dirt. Raw iron targets
natural iron/deepslate iron ore and requires a suitable stone-or-better pickaxe.
Coal targets natural ore; dirt targets dirt/grass blocks. Flint uses an unlimited
Baritone gravel-mining process under the outer skill deadline, explicitly
collects actual dropped flint, and can recycle owned gravel through normal
placement and mining. Gravel inventory is never counted as flint.
Stone/deepslate targets count their normal
cobblestone/cobbled-deepslate drops and require a suitable pickaxe. Completion
requires the inventory target; proximity or elapsed ticks alone do not count.
A mining skill fails after 400 ticks without inventory or position progress.

Crafting targets are overworld planks, sticks, a crafting table, a wooden
pickaxe, a stone pickaxe, a stone axe, a furnace, a bucket, and flint and steel.
Crafting uses unlocked vanilla recipes,
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

Early mining/crafting and table return have actual gameplay evidence. The newer
smelting, flint collection, and bucket-casting sequence require their own live
validation; see [verification](VERIFICATION.md).

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

## Smelting owned ore

`smelt:minecraft:iron_ingot` accepts a total inventory target, normally four for
one bucket and flint and steel. It remembers only a furnace it placed from an
owned furnace item, using ordinary placement, container transfers and vanilla
cooking. It will not collect output from an unrelated structure furnace.
`known_furnace` is world-scoped memory, not a claim of current reachability.
Completion reopens the owned furnace and requires a fresh server inventory
packet plus the requested iron in inventory. Fuel and cooking continue normally
after cancellation; the helper does not delete existing contents or stop time.

## Constructing a portal from natural lava and water

The active capability is `cast_portal:minecraft:nether_portal`. The former
structure-looting `portal` command is rejected and is no longer advertised.

```json
{
  "type": "skill",
  "id": "cast-new-portal",
  "skill": "cast_portal",
  "item": "minecraft:nether_portal",
  "target_count": 1,
  "max_ticks": 1800,
  "target": {"x": 4, "y": 63, "z": 2}
}
```

`target` is a natural lava-site landmark with bounded integer coordinates. The
executor observes loaded client cells again and never treats route coordinates
as proof of blocks. It requires an empty or water-filled bucket, flint and steel,
a stone pickaxe, and nine combined dirt/cobblestone blocks for temporary supports.
A lava-filled bucket alone does not satisfy the water-collection prerequisite.

The source scan examines at most 40,817 loaded cells within ±24 horizontally
and ±8 vertically, retaining at most 256 nearest water/lava candidates. Ten
exposed lava sources must be observed within twelve horizontal blocks of the
landmark. A dry, clear five-by-six work area with six blocks of headroom is
selected nearby; its predicted waterfall landings remain at least nine
horizontal Manhattan blocks from the observed lava sources. A bounded read-only
flow search also checks downhill spreading for each partial frame, including
temporary base holes. It rejects adjacent lava, unloaded terrain, more than
4,096 reached cells, or flow extending beyond 32 horizontal blocks or a
16-block drop. At most 64 otherwise suitable sites receive this check, with a
131,072-cell terrain cache. Headroom may contain replaceable, empty-collision
plants such as short grass; plants occupying a mold cell are broken normally
and server-confirmed before placement. Other plants are left for normal water
flow. Existing obsidian
or portal blocks cannot supply any part of the new frame.

Normal bucket use collects water. A nine-block mold creates a high water fork
and a dry standing pad; the helper places and removes its supports using normal
interaction/mining calls. It opens the two water channels before pouring, so
vanilla's first flow calculation sees every intended downward route. It casts two bottom blocks, two three-block side
columns, and two top blocks, for **ten newly formed obsidian blocks** and a
standard two-by-three interior. Each cast consumes an individually recorded
natural lava source and requires actual adjacent water. It verifies the exact
new obsidian cell after a server block update. The final top cast replaces the owned water source while touching its remaining
flow, so the source is consumed rather than recovered. The temporary interior
header is removed, remaining flow is observed until the interior drains, and
ordinary flint and steel ignites the completed frame.

Every fluid and placement action stays within player reach and uses a visible
raycast face, normal selected items, and normal client interaction APIs. There
are no direct world writes, inventory injection, container loot, or teleport
calls. The read-only server block marker holds at most 4,096 cell sequence
numbers and raw state IDs received in actual server packets. The expected
server state must match; a rejected placement cannot be confirmed from client
prediction alone. Action progression waits for actual server
updates rather than adding fixed cinematic delays.

Bucket pickup separately requires a fresh server hotbar packet containing the
filled bucket. Natural water may regenerate without invalidating that proof;
each lava source must additionally have a fresh server-confirmed non-source
state. The nine-slot tracker does not weaken the full-container synchronization
used for furnace completion.

`skill.verified_steps` records `lava_located`, `water_collected`, `portal_built`,
and `portal_lit` only from observed sources, owned water, ten individually
verified casts, and all six actual portal interior blocks. Construction is not
an inventory count. Normal pathfinding enters the portal; at most twelve normal
forward ticks can finish a close step into its plane. Inputs release while
waiting for vanilla portal dwell and during the five-second dimension-loading
grace. Only the actual `minecraft:the_nether` dimension completes the skill.

Cancellation releases controls and restores navigation settings; it does not
undo real blocks already placed or resources already consumed. A failed partial
construction remains in the world and is not silently claimed as a completed
portal. The roughly two-minute demonstration target is a performance goal, not
a fixed timer or a currently verified completion result.

## Fresh-world operator lifecycle

The authenticated local bridge accepts `world_unload`, `world_load`, and
`world_cancel`, each with a string `id`. Load also requires `save_name`, matching
`holospeed-demo-[A-Za-z0-9_-]{1,80}`. The folder must already exist directly under
Minecraft's saves directory with an ordinary `level.dat`; symlink folders and
path traversal are rejected. The mod does not copy, overwrite, or edit saves.

All responses use `type: "world_ack"`, the same `id`, `operation` (`unload`,
`load`, or `cancel`), `status`, nullable `reason`/`save_name`, and
`controls_armed: false`. Unload uses vanilla disconnect/save, then reports
`unloaded` only after the captured integrated-server **thread has terminated**.
Minecraft's `isStopped()` flag alone is insufficient because it is set before
shutdown saving. The helper also remembers servers observed during ordinary
play, covering native-menu unloads before an operator request.

Load calls vanilla `IntegratedServerLoader.start`. It reports `ready` only
when an alive Survival player and world are available and a normal pause menu
has actually paused the integrated world. The acknowledgement includes actual
`seed` (decimal string), `dimension`, `health`, `inventory_empty`, and
`game_mode`. The controller independently validates the loaded clone before
requesting the existing `arm` command; loading never starts gameplay itself.

Cancel acknowledges `cancelled` promptly and cancels the pending load response.
If vanilla loading is already underway, the helper retains exclusive lifecycle
ownership until the world arrives and is normally saved/unloaded. A late load
cannot emit `ready` or arm controls. Other world operations are rejected while
this cleanup is pending. F8, controller stop, and bridge disconnect apply the
same cancellation. A 120-second deadline reports `failed` while retaining this
quarantine; unexpected vanilla backup/recovery prompts are never approved
automatically. Restart/manual resolution may be required if vanilla never
finishes or invokes its abort callback. These operator operations are separate
from timed speedrun gameplay and contain no world editing or item injection.
