# Independent run review

This review checks recorded game state, asset hashes, screenshots, and the
executed protocol. It does not certify a competitive speedrun or replace the
Minecraft client as the source of state measurements.

## Development trial: 20261108T100936-acd35b07

**Judgment: Nether arrival is supported by both state and visual evidence.**
This trial preceded the requested final merge and must not be described as a
post-merge acceptance run.

- The initial snapshot at 180 ms, `aca9aa4f-7765-4aec-adf2-408599d28c6a`,
  contains **41 empty player inventory slots**, no verified steps, idle skill
  state, and a living Overworld player with health/hunger 20 at approximately
  `(40.5,66,35.65)`. Its compressed asset hash matches the journal. The initial
  screenshot also shows an empty hotbar and an empty hand. Thus the recorded
  agent trial began without tools, obsidian, or an ignition item in inventory.
- One bounded `portal` request targeted the declared ruined-portal landmark
  `(48,68,48)` with an 1,800-tick limit. The journal contains no raw movement,
  teleport, item-spawn, or world-edit command from the controller.
- Observed phases progressed through approach, chest opening and looting,
  obsidian placement verification, ignition, portal entry, and completed transfer.
- All seven milestone references resolve to full compressed state assets whose
  SHA-256 hashes match the journal. Their sampled cache ages were zero.
- Inventory first contained a fire charge at 12.011 seconds, then one obsidian
  and two fire charges at 12.507 seconds. Reopened, synchronized chest inventory
  supported the `chest_looted` checkpoint at 13.220 seconds.
- The repair checkpoint at 14.148 seconds coincided with consumption of the
  obsidian. Portal lighting at 14.817 seconds coincided with consumption of one
  fire charge. Retained client checks supported those later milestones.
- Portal dwell was observed from client tick 1102; the Nether snapshot arrived
  at tick 1291. This interval includes dimension loading and does not imply
  that the normal portal delay was skipped.
- Snapshot `fbec143f-b2b0-47e1-b9ba-bb75f3a0415b` at **24.665 seconds** records
  `world_ready=true`, `dimension=minecraft:the_nether`, health 20, and hunger 20.
  Health remained 20 in all 54 recorded state snapshots.
- The final recorded image at 25.301 seconds has a verified hash and shows
  Nether fortress surroundings through the portal overlay, plus the vanilla
  “We Need to Go Deeper” and “A Terrible Fortress” advancements.
- The goal event was recorded at **25.304 seconds**. There was one completed
  portal skill, zero recorded errors, and zero recoveries. Controls were released.

The checkpoint order reflects actual evidence arrival: acquisition can be
observed before the stronger reopened-chest synchronization check. No earlier
wood or stone-tool milestone was manufactured for this fresh-world portal route.

For presentation, the final image remains partly obscured by the portal overlay.
This does not contradict Nether arrival. A later move out of the portal should
be identified as a separate action after the recorded arrival, if performed.

## Post-merge acceptance run

Run `20261108T104247-2170a4ce` used merged main commit
`cdd6511a34d91611b9041ca7bc9dba2ad8076821` and a newly generated
`HoloSpeed Acceptance` world.

**Judgment: autonomous Nether arrival from an empty Survival inventory is
verified. The model handoff exposed a planning bug and used a local fallback.**

- Before start, an independent read of `level.dat` found Survival mode, Easy
  difficulty, commands disabled, bonus chest disabled, seed
  `-1950884254474434485`, an empty inventory, health 20, and no Nether advancement.
  The paused spawn screenshot also showed an empty hand and hotbar. The operator
  reported no gameplay input after the hosted dashboard start command.
- The initial recorded snapshot at **281 ms**,
  `5afde7fd-8d29-486f-b6e2-b790597a7373`, independently confirmed all **41 inventory
  slots empty**, idle skill state, no verified steps, health/hunger 20, and
  Overworld position `(42.5,70,31.5)`.
- Holo4 successfully selected the supplied portal candidate four times. Jev
  received a new state after each Holo4 response and explicitly rejected all
  four proposals. The subsequent Holo4 response exhausted its output limit
  without selecting a skill, so the controller recorded a local fallback and
  dispatched the bounded portal skill. This was **not a successful Jev
  authorization**. The observed model messages confused supplies needed after
  chest looting with prerequisites for starting the compound skill.
- The journal contains one gameplay skill request and only `state`, `observe`,
  and `stop` requests otherwise. All **66 compressed state asset hashes** and
  all **six screenshot hashes** match their recorded SHA-256 values. Player
  health remained 20 throughout.
- Inventory and synchronized chest checks verified ignition acquisition at
  26.307 seconds, obsidian acquisition at 26.893 seconds, and completed chest
  looting at 27.631 seconds. Obsidian was consumed for repair at 28.192 seconds;
  a fire charge was consumed for lighting at 28.842 seconds.
- The repair snapshot contains all **12 required noncorner obsidian frame
  blocks** and no portal interior. The lighting snapshot contains all 12 frame
  blocks plus **nine actual Nether portal interior blocks**. These are direct
  block samples, in addition to the executor's retained checkpoint flags.
- Portal dwell was observed from client tick 25956. Fresh snapshot
  `2db10e3f-159f-4074-b049-309b62e52e13` at **39.801 seconds**, tick 26149, records
  `world_ready=true`, `dimension=minecraft:the_nether`, zero cache age, and
  health/hunger 20. The interval includes ordinary portal dwell and loading.
- The verified final screenshot at **40.331 seconds** shows Nether fortress
  blocks through the portal overlay and the vanilla “We Need to Go Deeper” and
  “A Terrible Fortress” advancements. The goal event is also at 40.331 seconds.
  The controller released inputs and paused after one completed skill, four
  replanning recoveries, and one model-output error.

The independent judge sent no controls. The fixed-seed route acquired its actual
chest loot during the run; no earlier wood or stone-tool checkpoint was invented.
The model-validation issue should be corrected and demonstrated in a new fresh
run before presenting this as a clean Holo4-to-Jev handoff.

After the recorded goal, the operator reported that the player remained standing
in the Nether with controls released while the game continued running. The
Minecraft server log independently records “HoloSpeed was burned to a crisp while
fighting Ghast” at **10:44:11 UTC**, about 43 seconds after the goal event. This
later death does not alter the verified living Nether arrival, but the run must
not be presented as sustained safe survival after completion. The controller's
paused state does not itself pause Minecraft.

This fixed-seed trial is historical evidence. The next requested demonstration
uses a different seed and a full crafting, mining, and portal-building route;
that route requires its own fresh acceptance run.

## From-scratch launcher smoke: 20261108T114224-365835f0

This pre-merge smoke used commit
`1254e88a495f7efbff8f3f5249cd4b52109e78e3`. It is not the final merged-code
acceptance run.

**Judgment: fresh-world launch and initial conditions verified; gameplay blocked
by repeated model vetoes before any skill executed.**

- The hosted launcher created clone
  `holospeed-demo-ec770456548641e7923fa0613d7ead2b` from the prepared world on seed
  `-7131204740862824481`. Preparation took approximately 15.87 seconds, separately
  from gameplay timing. The journal links launch
  `ec770456-5486-41e7-923f-a0613d7ead2b` to template level hash
  `1388f793d7e9a905c0d750a7d7ee11c280a0504dae5a7aaf65a9ef51b735b269`.
- Independent clone NBT inspection confirmed Survival, Easy, commands disabled,
  bonus chest disabled, an empty inventory, full health, and Overworld spawn
  `(-6.5,65,-6.5)`. Initial recorded state
  `f520ca5a-c482-4a4c-90ed-33300275dfcc` at 113 ms also confirms **41 empty slots**,
  health/hunger 20, no verified steps, and the same position.
- Holo4 repeatedly selected gathering three acacia logs. Jev rejected the
  proposed skill eight consecutive times. The controller stopped at
  **24.827 seconds** with zero dispatched skills, zero milestones, and released
  controls. Its bounded rejection policy worked; progression did not begin.
- The candidate exposed a target 5.123 blocks away, beyond current interaction
  reach, without explicitly describing the navigation-first mining contract.
  The supplied local sample contained no fluid or hostile entity. These facts
  identify a contract/prompt issue to investigate; Jev's choice-only responses
  do not establish its internal reason for rejection.
- All **17 state asset hashes** and **eight screenshot hashes** match the
  journal; health remained 20 throughout those recorded snapshots. The journal
  contains only state, observation, and stop requests, plus the verified
  pre-start snapshot supplied by the launcher.

The independent judge sent no gameplay or lifecycle controls. At the end of
this smoke, full mining, crafting, smelting, ten-block casting, and living
Nether arrival were still pending for this route.

## Full-route acceptance: 20261108T115915-30150c0a

This run used merged main commit
`1a4374300b1b6cbfd48555324daa81cd7d74620e`. The hosted launcher loaded fresh clone
`holospeed-demo-3cd2d213bc3a4380a07fefca2bf75fde` from the same independently
verified empty Survival template on seed `-7131204740862824481`.

**Judgment: autonomous resource gathering, crafting, mining, smelting, and
construction setup verified. Full portal construction and Nether arrival did
not complete.**

Initial state `28d9074e-b01c-4a1b-91f7-9fc48e2c60c8` at **160 ms** contains
41 empty inventory slots, no verified steps, full health/hunger, and Overworld
position `(-6.5,65,-6.5)`. The launch journal links this state to the unchanged
template level hash recorded above. The operator reported no gameplay inputs
after starting the run. The independent judge sent none.

Recorded inventory and skill completions establish the following progression:

| Completed operation | Elapsed time |
| --- | ---: |
| Gather three logs | 21.205 s |
| Craft 12 planks, a table, and four sticks | 38.438 s |
| Clear obstructing vegetation, place the owned table, craft wooden pickaxe | 40.790 s |
| Mine stone and craft stone pickaxe | 99.785 s |
| Gather remaining stone and craft furnace | 141.620 s |
| Mine four raw iron | 169.443 s |
| Mine gravel and collect actual flint | 202.743 s |
| Smelt and collect four iron ingots | 246.228 s |
| Craft bucket | 271.895 s |
| Craft flint and steel | 275.290 s |
| Verify natural lava and collect water | 281.882 s |

The initial Holo4 selection was followed by an actual Jev approval using a newer
state before mining began. Across the run, Holo4 made 14 calls with median
latency **1.964 seconds**; Jev made 19 calls with median latency **213 ms**.
Four unexecuted model vetoes caused replanning. Fourteen gameplay skills then
completed before the casting skill encountered its first execution failure.

The casting skill built its temporary mold, opened channels, and placed water.
At **293.803 seconds**, its first lava-fetch phase failed with
`navigation stopped before reaching natural fluid source`. Baritone's native
log recorded 22 considered movements, an empty open set, and a one-node path
map. The player remained alive in the Overworld at approximately
`(-5.533,65,-17.137)`, standing in shallow flowing water. No obsidian frame block
had been cast, and neither `portal_built` nor `nether` was recorded.

All **835 compressed state assets** and **19 recorded screenshots** pass their
journal SHA-256 checks. Player health remained 20 throughout. The command
journal contains 15 bounded skill requests, state/observation requests, stop
requests, and normal pause requests; it contains no teleport, item-spawn, or
world-edit command.

The controller released controls, froze its result/display timer at
**293.869 seconds (4:53.869)**, and paused the actual singleplayer world. The
Minecraft server log at 12:04:09 UTC confirms “Saving and pausing game”. A native
screenshot corroborates the unobscured paused game view and frozen timer. The
two-minute target was not achieved, and this duration is a failed attempt's
duration, not a Nether-entry time.

## Follow-up fixes awaiting live acceptance

The subsequent changes batch stone-pickaxe and furnace materials into one
mining visit, add bounded walking out of shallow water that Baritone rejects,
and expose casting/navigation diagnostics. The parent reported **105 Java
tests** for the follow-up build. These checks do not establish end-to-end
gameplay success.

The judge independently checked the new escape-route geometry against the
recorded failed state. Its bounded search finds two safe waypoints from
`(-6,65,-18)`: `(-6,65,-19)` then `(-6,66,-20)`. The complete local sample confirms
solid floors, clear headroom, and a dry final cell with a water-free cardinal
margin. This validates a route in the recorded terrain; it does not validate
the live movement controller or the remaining ten-block casting sequence.

The user subsequently requested publishing the work. Judge monitoring stopped
without sending game controls. **The updated full casting route still needs a
fresh live run proving ten newly cast frame blocks, ignition, and a living
player in the actual Nether dimension.** Historical ruined-portal success must
not be used as evidence that this construction route has passed.

## Latest hosted run: 20261108T121602-417ab388

This run used the final tested working tree before its subsequent commit and
merge into main at `d7abfc35391522fe0f8b770dab520f7dd305210a`. It is not described
as a client restarted from that exact merge commit.

**Judgment: hosted New run worked without an operator pause before failure;
construction remains blocked after collecting the first lava bucket.** Initial snapshot
`72593781-9123-4634-98bd-b0cefd6aee27` at 87 ms has a verified asset hash and shows
41 empty inventory slots, full health/hunger, and the same Overworld spawn.

Thirteen resource and crafting skills completed before casting. The new
navigation recovery reached a natural lava source and collected an actual lava
bucket. At 267.159 seconds, returning to the casting pad failed with
`casting runoff waypoint is no longer reachable on verified terrain`. Recorded
diagnostics show **one lava source collected and zero frame blocks cast**;
the player remained in the Overworld with health 20 and the owned lava bucket.

The displayed result froze at **267.219 seconds (4:27.219)**. The journal contains
no operator pause event before this failure. The controller automatically
released controls and paused the world at 267.246 seconds. The independent judge
sent no controls. An operator end event occurred later, at 313.802 seconds; it
did not cause the failure. This is evidence that launch and first-lava acquisition work;
it does **not** establish a completed portal or Nether arrival. The user requested
publication at this point, and the remaining casting failure is retained here
for the next engineering pass.
