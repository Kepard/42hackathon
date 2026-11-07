# Vanilla1.20.4 input audit

Skills mining always walks right up to the block first (~1.5–2blocks, face width180px at480px frame height, scaled at other resolutions). Jev uses the same screenshot Box/visibility proposals; non-Jev uses selection-face geometry. Short forward steps stop on unchanged views or lower-centre lava/blue drop/water pixel cues. These conservative heuristics are not safe pathfinding. Uncertain/stuck/dangerous approaches return `not_close` with a seek-first hint, without attacking/probing. Straight-up/down mining never walks. Missing outlines permit the bounded mining-progress probe only after close size is established. `seek` distinguishes close+outline (`in_reach`) from close without an outline (`close_unconfirmed`), which requires the mining probe. Never back away merely to find an outline.

RAW and SKILLS (including Jev) expose the same low-level keyboard and mouse inputs.
All inputs return pixels only, not game-state data. Prefer seek for block approach.

| Vanilla action | Input path | Previous gap |
|---|---|---|
| WASD, jump, sneak, sprint | key_down/up/press; skill move/reposition | Generic keys absent from skills |
| Attack/destroy, use/place | left/right mouse_down/up/click | Existing |
| Pick block | middle mouse | Missing |
| Q drop, Ctrl+Q stack, F offhand, E inventory, 1–9 hotbar | keys; hold Ctrl before Q | Ctrl polling/modifier mask missing |
| Wheel, GUI scrolling, spectator wheel | scroll(vertical,horizontal) | Missing |
| T chat, / command screen, Tab player list/autocomplete | keys + type_text + Enter | Slash absent; commands now blocked at send boundary |
| F1 HUD, F2 screenshot, F3/debug chords, F5 perspective, F11 fullscreen | F1–F25, modifiers held before second key | Most function keys absent; F3 physical polling ignored synthetic hold |
| Escape, L advancements, P social, smooth camera | named keys; bind unbound smooth-camera action in vanilla Controls | L/P not advertised; GUI bypassed global Keyboard handler |
| Creative hotbar save/load (C/X+number) | held keys, subject to vanilla Survival restrictions | C/X not advertised |
| GUI left/right/middle click, shift-click, double-click | click_at(button,count), shift_click_at | Right/middle/double click absent |
| Drag distribution/splitting | mouse_move_to -> mouse_down -> mouse_move_to across slots -> mouse_up | GUI mouseDragged not dispatched |
| Number over GUI slot, Ctrl+A/editing, modifier combinations | mouse_move_to + held keys, navigation/edit keys | Generic GUI keys/modifiers absent or not propagated |

Key names include all letters/digits, punctuation (`/`, `[`, `]`, `;`, etc.),
F1–F25, both sides of Shift/Ctrl/Alt/Super, navigation/edit/lock keys and keypad.
Press modifiers/F3 first, then press the second key; release in reverse order.
Left/right modifier holds are independent. Release all held inputs when done.
Vanilla permission/GUI restrictions still apply: exposing a key does not grant
Creative or operator powers. F3+C's intentional crash chord is still a vanilla
key sequence; do not hold it for ten seconds during a trial.

All submitted slash commands are **blocked**, not scored as successful actions:
the mod intercepts both command-send methods and slash-prefixed chat messages.
The refusal appears in vanilla chat pixels. Normal chat and recipe/book text
remain allowed. No clipboard or F3 data is returned through an input tool.

GUI gestures and modifier masks have fake-client/pure-Java regressions. New
mixins require the next Minecraft restart; do not restart an active successful
run merely to refresh the input vocabulary.
