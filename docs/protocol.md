# HoloSpeed bridge protocol v1

The Fabric client connects to `ws://127.0.0.1:8765/bridge` with
`Authorization: Bearer <HOLOSPEED_BRIDGE_TOKEN>`. The server accepts one game.
JSON messages use `type`; all command IDs are unique UUID strings.
Only screenshots, frame IDs, dimensions, ticks and command acknowledgments cross
the bridge. No coordinates, inventory, block or entity queries are exposed.

## Client → controller

* `{"type":"hello","protocol":1,"minecraft":"1.21.1"}`
* `{"type":"observation","id":"request-id","frame_id":"unique-frame-id","tick":120,"width":640,"height":360,"image_base64":"...","mime_type":"image/png","screen_open":false}`
* `{"type":"ack","id":"action-id","status":"completed","start_tick":120,"end_tick":126,"reason":null}`. Status: `completed`, `rejected`, `cancelled`. Rejection reasons are diagnostic strings.
* `{"type":"event","name":"emergency_stop","detail":"F8 pressed"}`

## Controller → client

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

The controller permits at most one observation or action in flight, and waits
for completion before observing again. Holo4 proposes a plan with controls
released; Jev then validates that unexecuted proposal against a new screenshot.
Only the fresh frame ID may authorize execution. A slow plan never extends an
old frame's input lifetime. Without Jev validation, results beyond the frame age
budget are discarded. Resuming requires a fresh screenshot and replan.
