# Structured client state

The user-approved state-aware mode adds read-only game state to the existing
visual observation and input protocol. It does not change `observe`, `action`,
or their frame freshness rules. Reading state does not issue controls, load
chunks, access a server world, or invalidate a pending visual observation.

Request over the existing authenticated loopback WebSocket:

```json
{"type":"state","id":"state-0001"}
```

`id` must be a nonblank string of at most 128 characters. The response is a flat
`game_state` object with the same `id`:

| Field | Meaning |
| --- | --- |
| `tick` | Client tick when the snapshot was sampled, not response time. |
| `sampled_at_ms` | Epoch milliseconds at sampling. |
| `cache_age_ms` | Monotonic age of the reused sample at response time. |
| `world_ready` | Both client world and player exist. |
| `screen_open` | A vanilla screen was open when sampled. |
| `dimension` | Dimension registry ID, or null without a world. |
| `player` | Position, eye position/height, yaw, pitch, velocity, on-ground, health, hunger, selected hotbar slot, block interaction range. Null without a world. |
| `inventory` | All native player inventory slots, including empty slots. |
| `screen` | Screen type/title, dimensions, handler sync ID, cursor stack, slots and container origins. Null when no screen is open. |
| `crosshair` | `miss`, `block`, or `entity`, with target position/distance and relevant block position/ID or entity ID. |
| `nearby_blocks` | Up to 512 interesting blocks in an eight-block-radius sphere around the player's block position. |
| `nearby_blocks_truncated` | More matching blocks exist inside that sphere than the cap permits. |
| `nearby_radius` | Fixed at 8. |
| `local_blocks` | Up to 729 cells in the inclusive nine-by-nine-by-nine cube centered on the player's block position, **including air**. |
| `local_bounds` | Inclusive `min`/`max` block positions and `complete`; null without a world. |
| `nearby_entities` | Up to 64 tracked entities within eight blocks of the player, nearest first. |
| `nearby_entities_truncated` | More tracked entities matched than the cap permits. |
| `skill` | Current local skill status; attached at response time, independently of the state cache. |

Vectors and block positions use `{x,y,z}`. Inventory entries use
`{slot,item,count}`, with registry IDs such as `minecraft:birch_log`. Empty
stacks are `minecraft:air` with count 0. Native player inventory slot indices
are 0–40 (hotbar 0–8, main inventory 9–35, armor 36–39, offhand 40).
`player.selected_slot` is **zero-based**, unlike the existing action `hotbar`
field, which is one-based.

`screen` contains `type`, `title`, `width`, `height`, `sync_id`, `cursor`, and
`slots`. Noncontainer screens have null `sync_id`/`cursor` and no slots.
Container screens also have `origin_x`, `origin_y`, and `slots_truncated`.
A screen slot is `{slot,item,count,x,y}`; `slot` is the ScreenHandler slot ID,
**not** the player inventory index. Its `x,y` are GUI pixels relative to the
container origin. Slot centers are `(origin_x+x+8, origin_y+y+8)` in the screen's
`width`/`height` coordinate space. `cursor` is `{item,count}`. Screen titles are
bounded to 256 characters and at most 256 slots are returned.

Blocks have `{position,id,solid,full_cube,fluid,hardness}`. `solid` means the
collision shape is nonempty; `full_cube` means it exactly equals a unit cube.
These are collision summaries, not complete voxel geometry. `fluid` is a fluid
registry ID, including `minecraft:empty` and flowing variants; `hardness=-1`
means unbreakable. Nearby interesting blocks include logs, leaves, base stone, fluids, gravel,
tables, furnaces, chests, portal/frame blocks, and iron/coal ore. Interactable
objects and portal/ore resources are prioritized before ordinary terrain within
the same 512-record cap; each group is ordered by squared block-offset distance,
then y, x, z. This is a filtered target list,
**not** a complete map. Use `local_blocks` for bounded collision checks.

Only loaded client chunks are sampled. Missing local cells make
`local_bounds.complete=false`; missing cells must be treated as unknown, never
as air. The sphere examines at most 2,109 positions. The local cube examines
729 positions and reuses block records where the two scans overlap. No world
NBT, entity NBT, full chunk serialization, or chunk loading is performed.

Entities have `{id,type,position}`, optionally `health` for living entities and
`stack:{item,count}` for dropped item entities. IDs are local runtime entity
IDs, not persistent identities.

The client thread samples on demand at most once per 200 ms, then returns deep
copies with distinct request IDs. World/player replacement invalidates the
cache immediately, so a disconnected world or respawn never returns a previous
player snapshot. A successful explicit operator `arm` also invalidates the
cache so planning cannot reuse a just-closed pause screen. Without a world, arrays are empty, player/dimension/local
bounds are null, and the crosshair is a miss. The uncached `skill.known_table` field is a separate world-scoped table memory;
its bounded loaded-chunk discovery and meaning are documented in
[bounded skills](SKILL_PROTOCOL.md). It does not imply current reachability or
add cells to the local map. Consumers must evaluate sample
age and action preconditions themselves; a valid read does not authorize input.
