# HoloSpeed bridge protocol v1

The Fabric client connects to `ws://127.0.0.1:8765/bridge` with
`Authorization: Bearer <HOLOSPEED_BRIDGE_TOKEN>`. The server accepts one game.
JSON messages use `type`; the controller generates unique command IDs. Version 1
retains screenshot/input messages and now also carries structured client state
and bounded normal-mechanics skills. `GAMEPLAY_MODE=state` uses these new messages;
the earlier visual-control mode remains compatible.

## Structured state and skill messages

Read state with `{"type":"state","id":"state-request-id"}`. The response is a
flat `game_state` object with the same `id`, not a nested state envelope. It
includes client tick, sampling timestamp, cache age, world/dimension readiness,
player coordinates/orientation/health, native inventory slots, current screen
and slots, crosshair target, sampled blocks/entities, and current skill status.
Only already-loaded client chunks are sampled; no server-world or chunk-loading
queries occur.

The client samples at most every 200ms. Its local cube contains up to 729 cells,
including air, with explicit bounds/completeness. Nearby interesting blocks are
filtered and capped at 512; this list is not a complete collision map. Missing
cells remain unknown. The consumer checks sampling/cache age; a new response ID
does not make an old sample fresh. Native `player.selected_slot` is zero-based
(0–8), unlike raw `action.hotbar` (1–9). GUI handler slot IDs also differ from
native inventory indices. See the exact [state schema](../fabric-mod/STATE_PROTOCOL.md).

Start a local skill with:

```json
{"type":"skill","id":"skill-request-id","skill":"mine","item":"minecraft:birch_log","target_count":3,"max_ticks":1200}
```

For `mine` and `craft`, `target_count` is the total desired inventory count
(1–64), not an additional quantity. `max_ticks` is 20–1800, with
a wall-clock watchdog as well. The mod uses a small target allowlist, Baritone
for navigation/mining/pickup, and ordinary recipe/screen-handler APIs for crafting.

The immediate `skill_ack` echoes `id`, supplies `skill_id`, and has status
`accepted`, `completed` (already satisfied), or `rejected`, with diagnostic reason
and current skill snapshot. An accepted acknowledgment does not prove acquisition.
Poll `game_state.skill` for `active`, `status`, `phase`, counts, deadlines, and
outcome. Independently check the fresh inventory postcondition for these skills.

Statuses are `idle`, `running`, `completed`, `failed`, or `cancelled`. A terminal
status may still have `active=true` while `phase=cancellation_pending` retries
cleanup; control ownership is not released until cleanup finishes. Optional
`skill.keys=null` means readback unavailable, while `[]` means verified no held
keys. Full target, recipe, table-placement, and cancellation details are in the
[skill schema](../fabric-mod/SKILL_PROTOCOL.md).

`skill.known_table` is a nullable block position remembered from a real table
interaction/placement. It is scoped to the client world and does not insert a
table into current block samples or prove a route is clear. Returning to a
remembered table requires Baritone and a longer bounded crafting deadline.
`skill.capabilities` is a unique, bounded list of exact `kind:namespace:target`
pairs. An omitted list supports only the existing early-game behavior; the
controller never assumes a new skill from a generic label.

### Portal route and verified steps

The advertised `portal:minecraft:nether_portal` capability accepts:

```json
{"type":"skill","id":"portal-request-id","skill":"portal","item":"minecraft:nether_portal","target_count":1,"max_ticks":1800,"target":{"x":48,"y":68,"z":48}}
```

This is the Java 1.21.1 route for declared seed `-1950884254474434485`, selected
only with `RUN_GOAL=nether`, the matching declaration, Overworld state, and the
required capability. The client verifies the actual site and uses normal chest
transfers, block placement, item use, and movement. Expected geometry is route
knowledge, not a replacement for those checks. This candidate's postcondition
is a fresh ready/alive player in `minecraft:the_nether`; `target_count:1` does
not represent an obtainable portal item. The skill's `inventory_count` therefore
does not prove completion.

Portal phases include approaching/looting the chest, approaching/repairing the
frame, ignition, and entry. `skill.verified_steps` is an optional list, default
`[]`, with at most six unique values:

```json
["portal_located","chest_looted","obsidian_acquired","ignition_acquired","portal_repaired","portal_lit"]
```

The mod emits them only after its geometry, inventory, server synchronization,
or portal-block check succeeds. In particular, a chest opening or an optimistic
client slot update alone is not `chest_looted`; the normal transfer must produce
relevant owned inventory after authoritative synchronization. Acquisition steps
remain recorded after consuming the item. The controller may also confirm
obsidian/ignition acquisition directly from current owned items.

Phase names and terminal status never imply these steps. Milestones retain
their state ID, tick, inventory, dimension, and verified-step evidence. The
final `nether` milestone is independent and requires the actual dimension.

Raw actions cannot overlap a skill. Skill starts are rejected while an action
or observation capture is pending. State polling remains available during skill
execution. The existing `stop` message cancels both raw actions and skills,
including Baritone pathing and input overrides. F8, focus loss, disconnect,
unexpected world/player changes, death, pause, and deadlines also cancel local
work. Expected transfer during an active portal skill releases controls, then
allows up to five seconds for fresh destination state. It does not reuse old
coordinates or old inventory as destination evidence. Other world changes pause
the controller.

## Explicit operator rearm

An operator Start/Resume uses `{"type":"arm","id":"request-id"}` before
starting an idle live controller. A successful reply is
`{"type":"arm_ack","id":"request-id","status":"ready"}`; a rejection
includes status/reason and must not start gameplay. The request has a five-second
controller deadline. The mod requires a ready world, living player, and focused
window; it may close the pause menu or an empty inventory/crafting screen.
Occupied crafting grids/cursors and unrelated screens reject the request.

F8 releases controls and latches local execution off. F9 locally reenables the
mod and requests controller start; HTTP/dashboard Start/Resume instead performs
the guarded `arm` exchange described above. Autonomous skills and model
responses cannot rearm themselves.

Hosted controls use a separate HTTP command queue, not direct access to the
Fabric WebSocket. `start_new`, `resume`, `pause`, and `stop` require the site's
private `CONTROL_TOKEN`; the publisher pulls/acknowledges them with
`INGEST_TOKEN`. UUID identity, a 30-second dispatch deadline, and a durable local
claim prevent automatic replay after an uncertain interruption. `start_new`
archives the current recording and starts another in the same world; `stop`
finishes/archives it. See [dashboard setup](../dashboard/README.md) for endpoints
and the private operator link.

## Retained screenshot and input protocol

### Client → controller

* `{"type":"hello","protocol":1,"minecraft":"1.21.1"}`
* `{"type":"observation","id":"request-id","frame_id":"unique-frame-id","tick":120,"width":640,"height":360,"image_base64":"...","mime_type":"image/png","screen_open":false}`
* `{"type":"ack","id":"action-id","status":"completed","start_tick":120,"end_tick":126,"reason":null}`. Status: `completed`, `rejected`, `cancelled`. Rejection reasons are diagnostic strings.
* `{"type":"event","name":"emergency_stop","detail":"F8 pressed"}`

### Controller → client

* `{"type":"observe","id":"request-id"}`: capture a fresh framebuffer on the render thread; encode a maximum-width 960px image off thread.
* `{"type":"action","id":"action-id","frame_id":"source-frame-id","ttl_ms":2000,"action":{"keys":["forward","sprint"],"duration_ticks":6,"yaw_delta":0,"pitch_delta":0,"hotbar":null,"cursor":null,"scroll":0}}`
* `{"type":"stop","reason":"operator pause"}`: immediately release inputs and cancel outstanding action.
* `{"type":"status","status":"running","objective":"Find a tree","strategy":"Gather logs","holo_latency_ms":600,"jev_latency_ms":80,"elapsed_ms":12345,"milestones":[],"event":"..."}` updates the in-game HUD.

Keys: `forward`, `back`, `left`, `right`, `jump`, `sneak`, `sprint`, `attack`,
`use`, `inventory`, `drop`, `swap`, `escape`. Keys are a complete held-input
state; inventory/drop/swap/escape are single pulses. World camera deltas are
degrees, bounded to ±90 yaw / ±60 pitch per action. Duration is 1–20 ticks,
except stationary mining may last up to 80 ticks: keys exactly `["attack"]`,
zero camera/scroll, null hotbar/cursor, and no GUI open. This permits bare-hand
logs to finish breaking without an inference gap resetting block damage.
`hotbar` is null or 1–9. `cursor` is null or
`{"x":0.5,"y":0.5,"button":"left"}` with coordinates normalized to the
entire screenshot; button is left/right/middle. GUI cursor actions only apply
when a screen is open. `scroll` is an integer −9…9.

The mod rejects actions based on an unknown/expired frame, overlapping actions,
invalid fields or duplicate IDs. Frames expire after 15 seconds; `ttl_ms` is an
additional maximum age of that source frame (bounded 100–15000 ms). Stop and
disconnect release all held controls. F8 pauses locally. Actions have a tick
deadline plus a wall-clock watchdog. No queues of future actions are permitted.
An ACK means controls were executed, not that the gameplay objective succeeded.

The legacy visual controller permits at most one observation or raw action in
flight and waits for completion before observing again. Holo4 proposes a plan with controls
released; Jev then validates that unexecuted proposal against a new screenshot.
Only the fresh frame ID may authorize execution. A slow plan never extends an
old frame's input lifetime. Without Jev validation, results beyond the frame age
budget are discarded. Resuming requires a fresh screenshot and replan.

In state mode, Holo4/Jev choose among validated high-level candidates instead.
The controller rereads state after inference, starts one eligible skill, polls
its local progress, and verifies the candidate's inventory or dimension
postcondition. Cloud inference does not time the
skill's key presses. See [architecture](architecture.md) and
[provider contracts](providers.md) for this division of responsibility.
