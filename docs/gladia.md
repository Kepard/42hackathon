# Gladia voice steering

The live dashboard has a microphone button beside the game feed. Unlock operator
access at the bottom of the page, click **Talk to the agent**, and grant microphone
permission. Partial transcription appears as a caption; only finalized utterances
enter the authenticated command queue. The text field uses the same command path
when a microphone is unavailable.

Supported interaction:

- **“Pause the game”** releases agent controls and pauses an available local
  singleplayer world. **“Resume”** rearms the bridge and continues the existing
  recording with fresh observations. **“End the run”** stops and archives it.
- Spoken objectives such as **“Gather wood before going further”** become bounded
  operator guidance for the next planning step. Holo4 and Jev consider this
  guidance among available, locally eligible survival skills. It does not bypass
  prerequisites, create an unimplemented skill, or change the run's configured
  goal implicitly.
- Questions such as **“Why are you mining iron?”** start a separate, bounded Holo4
  answer grounded in the latest recorded controller state. The reply appears as
  text beside the game. Questions do not block pause or stop commands. When no
  recording is active, the controller returns its recorded status locally.

Control phrases use conservative matching: “don't pause”, “why did you pause?”
and “pause after crafting” are not immediate pause operations. A simple “stop”
pauses; ending a recording requires an explicit phrase such as “end the run”.
Actual command acknowledgement and the recorded reply distinguish a queued
request from completed execution.

## Setup

Set `GLADIA_API_KEY` as a **secret** in the dashboard's Sites runtime environment,
then deploy a saved version to apply that environment revision. The local
publisher still needs its existing Sites and ingest capabilities; the browser
uses the separate operator capability after password login or a private link.
No Gladia API credential is embedded in browser assets or returned to visitors.
The optional local `.env` entry is used only for separate integration checks.

The Worker makes one authenticated `POST https://api.gladia.io/v2/live` request
for each explicit microphone start. It requests Solaria-1, 16 kHz mono PCM16,
300 ms endpointing, and partial/final/lifecycle messages. The browser receives a
session-specific WebSocket URL and sends raw audio directly to Gladia. It waits
for Gladia's `start_recording` or `start_session` lifecycle before upload; an
explicit `audio_upload_allowed: false` stops recording. Current live testing
returned a bare `start_recording` event without a `data` object.

The browser converts microphone samples through a same-origin AudioWorklet into
100 ms little-endian PCM chunks. Recording ends after two minutes, excessive
socket backpressure, a provider/network error, locking controls, leaving the page,
or an explicit stop. Stopping releases microphone tracks immediately, sends
`stop_recording`, and permits up to three seconds for final transcription before
closing the socket. There is no automatic microphone restart or session creation.

Session initiation uses an immutable UUID and durable creation claim, a bounded
lease, and a cooldown. An ambiguous upstream response is never retried
implicitly. The ephemeral WebSocket URL is returned only to the authenticated
operator and is not stored in R2 or published in run data.

## Command and evidence flow

Final text carries an immutable command UUID, the run ID captured when the
utterance was heard, the Gladia session ID, and the provider utterance ID. The
Worker's existing authenticated queue, 30-second expiry, and publisher's durable
ledger apply. A changed run rejects delayed guidance. Repeated command or
utterance identities cannot dispatch a second control action. Typed messages use
`session_id: "typed"` and a new utterance UUID.

The local `POST /api/voice` endpoint accepts only that bounded text command.
`voice_received`, `voice_guidance`, and `voice_reply` events preserve the actual
interaction while a recording is open. Holo4 questions also emit real `inference`
lifecycle events and keep redacted provider requests/responses in the local run
journal. Their role is `voice_response`, their input source is `recorded_state`,
and they never claim screenshot access or successful gameplay actions. Public
viewers can read published captions and replies but cannot record or send commands.

The project does not write raw microphone audio to its local run directory or
browser storage. Gladia receives the audio as the configured transcription
provider. Session and provider errors stop capture and retain an explicit error
state; they never generate a simulated transcript.

## Verification

On 2026-11-08 an actual Gladia v2 request returned HTTP 201. A 1.365-second locally
synthesized speech sample, followed by one second of silence, produced partial
transcripts and the final text
**“Pause the game, please.”** with utterance ID `00_00000000`, followed by a clean
`end_session`. This test did not use a microphone or send a Minecraft command.
Two preceding diagnostic sessions exposed the readiness-event mismatch before
the corrected test succeeded. Credential-free evidence is retained locally in
`runs/evaluations/gladia/synthetic-live-check.json`.

Automated checks cover server authentication/session idempotency, PCM conversion,
provider readiness, partial/final handling, microphone cleanup, stale run binding,
negation-aware controls, transcript deduplication, publisher crash handling,
and background questions that cannot delay pause. Browser microphone permission
and hardware capture have not been manually verified in this environment.

References: [Gladia session initiation](https://docs.gladia.io/api-reference/v2/live/init),
[WebSocket protocol](https://docs.gladia.io/api-reference/v2/live/websocket), and
[streaming quickstart](https://docs.gladia.io/chapters/live-stt/quickstart).
