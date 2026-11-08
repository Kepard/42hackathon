# HoloSpeed live observer

Public dashboard: [open the site](https://holospeed-live-kepard.kepard.chatgpt.site).
The initial source was copied from the Sites project at
`81345ddd0107d99fe1c2c57dff37e38428577f1c`. The interface shows the Minecraft
window, controller intent, input telemetry, model latency, metrics, milestones,
and searchable run events. Public viewers can watch; a separate private
operator capability enables run controls.
The **On a mission** pipeline shows actual Holo4 and Jev request lifecycles,
their recorded input sources and decisions, and the local skill executing next.
Animation requires a fresh, active request or skill; completed decisions remain
visible without implying continuous inference. Technical request details expand
on demand.

**Devin engineering** lets an operator prepare and review an exact redacted run
brief, then explicitly create an investigation capped at 2 ACU. Public viewers
can inspect published findings and proposed tests. Briefs stay private, duplicate
submissions are prevented, and proposed code is never applied automatically.
See [the engineering workflow](../docs/devin.md).
The resource panel shows the active skill and matching progress alongside the
latest recorded inventory, health, hunger, and position. These player samples
are taken at skill boundaries and display their recorded time and tick; older
runs without structured samples retain an explicit empty state.

## Build and hosting

Node.js 22 or newer is sufficient; no dependencies need installing.

```sh
cd dashboard
npm test
npm run build
```

The build writes `dist/server/index.js`, a Cloudflare Worker ES module containing
the API and UI assets, and `dist/.openai/hosting.json`. The checked-in hosting
manifest identifies the existing Sites project and requests its `BUCKET` R2
binding; it contains no credential. Publish the generated `dist` artifact through
that project's Sites hosting workflow. Building locally does not publish.

Viewer access follows the Sites project sharing settings. The current project
is public; publishing preserves that setting. For private hosting, restrict
viewers in Sites settings. Configure two independent runtime secrets:

- `INGEST_TOKEN`: publisher authentication, matching local
  `HOLOSPEED_INGEST_TOKEN` (at least 16 characters).
- `CONTROL_TOKEN`: private operator capability (32–256 URL-safe characters),
  distinct from `INGEST_TOKEN`.

Keep secrets out of source and browser assets. A public viewer needs neither
secret. Other hosting platforms must provide the `BUCKET` binding and any
desired viewer access layer.

## Operator access

Privately share `https://<site>/#control=<CONTROL_TOKEN>` with the operator.
The browser removes the fragment immediately and retains the capability only
in that tab's session storage. **Lock controls** removes the saved capability.
Anyone with that private link can control the agent; rotate `CONTROL_TOKEN` in
hosting settings to revoke it. Never publish the operator link with the public
viewing URL.

**New recorded run** archives the current recording and starts another in the
current Minecraft world; it does not reset the world. **Resume** continues the
current run, **Pause** releases controls, and **Stop & archive** ends it. Selecting
an archived run only changes the journal being viewed.

The UI posts an immutable UUID command with operator authentication. The Worker
accepts at most one pending command, with a 30-second delivery window. The local
publisher polls using `INGEST_TOKEN`, executes controller operations, and records
a completed/failed result. Browser feedback distinguishes acceptance from
completion; uncertain requests are checked by ID and never resubmitted
automatically. The publisher's persistent ledger prevents restart replay of an
operation whose outcome is unknown. Expired commands are not dispatched; a late
acknowledgement may still record an operation's actual result.

## Broadcast the local game

Install the controller from the repository root (`uv sync`) and have its local
HTTP server and Minecraft 1.21.1 running. The publisher requires Linux/X11,
`xwininfo`, and FFmpeg with `x11grab`; it selects the named Minecraft window on
the chosen display, with no desktop-capture fallback. H.264 fMP4 HLS video is the
primary feed, scaled to 960 pixels wide, independently of planner observations.
The publisher uses the display refresh rate by default, with an explicit
`--video-fps 60` option. JPEG capture remains a one-FPS fallback (`--fps 1`).

Set these environment variables, or place them in the repository's ignored
`.env` file:

- `HOLOSPEED_SITE_TOKEN`: Sites gateway access token (also supports private hosting).
- `HOLOSPEED_INGEST_TOKEN`: the matching Worker `INGEST_TOKEN` secret.

From the repository root:

```sh
uv run --env-file .env python -m holospeed.broadcast \
  --site-url https://holospeed-live-kepard.kepard.chatgpt.site \
  --controller-url http://127.0.0.1:8768 \
  --display :97 \
  --video-fps 60 \
  --runs-dir runs
```

Choose the controller port, display, and run directory used by your session.
The publisher defaults to controller port `8768`, display `:97`, directory
`runs`, one-FPS JPEG fallback, and display-refresh video capture. The controller itself
normally defaults to port `8765`, so pass that URL when appropriate. The
publisher reads its tokens from the process environment; `--env-file` above is
handled by `uv`. It accepts only a loopback controller URL and an HTTPS site
URL. Stop it with Ctrl-C; this does not stop the agent or Minecraft.

The browser uses native HLS where available and the vendored Hls.js player
otherwise. Playback is muted, with a play button if autoplay is blocked.
The FPS meter measures presented frames using `requestVideoFrameCallback`;
the capture target is shown separately and is not a claim about game rendering
or delivered FPS. Approximate stream delay uses HLS program timestamps when
available. A live badge requires advancing playback and fresh captured footage,
not just a recent metadata heartbeat. Camera and agent status remain separate:
a paused agent can have live video. Telemetry and JPEG fallback poll every second.

The publisher uploads initialization/segment assets before the playlist. The
Worker limits each video asset to 4 MiB and playlists to 64 KiB, validates
same-generation references, and retains approximately 12 recent segments.
Video playback uses a rolling buffer, not a full-run video archive.

## Archive scope and evidence

Complete original journals and screenshots remain in local `runs/<run-id>/`.
Cloud archives are sanitized, bounded copies: credentials and inline image
payloads are removed, while ordinary model request/response text is retained.
The broadcaster advertises the 20 most recent runs and uploads their detail
journals approximately every five seconds. The current state contains the
controller's recent event window; selecting a run loads its separately uploaded
archive. Downloaded JSON contains that hosted copy, not the complete local run.

Hosted run uploads are limited to 8 MiB and 30,000 events. A local JSONL record
over 24 MiB or a sanitized journal exceeding the upload budget stops further
archive publication for that journal. The site may then retain an older copy
or have no detail archive; the complete local journal is unaffected. Invalid
JSONL records are skipped and an incomplete trailing record waits for completion.
Metadata is capped at 240,000 bytes by the publisher; recent events and latency
history are dropped from an oversized snapshot before retrying. The Worker
also enforces payload complexity and text-size limits. These archives are for
remote inspection, not full-fidelity backup or frame replay.

The Worker's strict `STATE_FIELDS` schema accepts the controller's current
hybrid snapshot: status, objectives, input keys, a skill descriptor in `action`,
latency, metrics, milestones, recent events, and latency history. Skill progress
and compact structured evidence travel within event data; full game-state
assets remain local. New top-level snapshot fields require a coordinated API
schema update, since unknown fields reject the snapshot. For hybrid runs,
`metrics.actions` counts completed skills; for earlier runs it counts completed
input actions. Milestones distinguish game-state confirmation, simulation,
and model visual claims; model claims require review.

`web/CalSansVF.woff2` is distributed under the SIL Open Font License retained
in [web/OFL.txt](web/OFL.txt). Hls.js 1.6.13 is vendored from its official npm
release; see [web/HLS-VERSION.txt](web/HLS-VERSION.txt) and
[web/HLS-LICENSE.txt](web/HLS-LICENSE.txt). No third-party fonts or scripts are
fetched at runtime.
