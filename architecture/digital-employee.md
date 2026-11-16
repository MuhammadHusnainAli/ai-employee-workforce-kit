# Digital Employee Capabilities

This document covers what makes an AI employee work like a real colleague:
- it joins **Zoom, Microsoft Teams and Google Meet** calls, listens, and **answers people by voice**;
- it works in **Google Chat, Slack and Teams chat**;
- it **takes notes** and turns them into tasks;
- it **writes code on its own laptop**;
- it has **its own accounts**, a calendar, a manager and a place in the org chart.

Diagrams: **5 · Meetings & Real-time Voice** and **6 · Workspace & Digital Identity** in
[diagrams/ai-workforce-architecture.drawio](diagrams/ai-workforce-architecture.drawio).

> **Transparency is part of the design.** An AI employee uses the same accounts, tools, calendar and laptop as a
> human colleague, but it is **always identifiable as AI**: its display name, its profile, and an announcement when
> it joins a meeting. Recording-consent laws in many jurisdictions and AI-transparency rules (e.g. EU AI Act
> Art. 50) require this, and hidden AI participants destroy customer and staff trust once discovered. The kit makes
> the experience seamless, not deceptive. See [ADR-0010](adr/0010-ai-employees-are-disclosed.md).

---

## 1. New services (per cell)

| Service | Owns | DB | Key points |
|---|---|---|---|
| **Meeting Service** | Meeting attendances, bot sessions, transcripts, notes, action items | `meeting_db` (pg-ledger) | One `MeetingAttendance` Temporal workflow per employee per meeting supervises a bot pod |
| **Realtime Voice** | Nothing persistent (stateless workers) | n/a | Pipecat streaming pipeline VAD → STT → turn-taking → LLM → TTS, running next to the bot on GPU nodes |
| **Workspace Manager** | Each employee's laptop: VM, volume, lifecycle, live-view sessions | `workspace_db` (pg-app) | One persistent Kata microVM (or KubeVirt VM) per employee |
| **Identity & Accounts** | The employee's identity and every external account (mail, calendar, chat, git, apps) | `identity_db` (pg-app) | Provisions and de-provisions through SCIM 2.0 and admin APIs. Secrets in OpenBao |
| **GPU Inference Pool** (platform) | n/a | n/a | faster-whisper STT, Kokoro TTS, pyannote diarization, vLLM. Autoscaled on queue depth |

New event stream `MEETING` with subjects `meeting.scheduled`, `meeting.joined`, `meeting.transcript.segment`,
`meeting.left`, `meeting.notes_ready` and `meeting.action_item`.

## 2. Meetings

### Lifecycle (Temporal workflow `MeetingAttendance`)
Workflow ID = `platform:meeting_id:employee_id`, so a second "join" for the same meeting is rejected and an
employee can never be in a meeting twice.

1. **Trigger.** A calendar invite reaches the employee's mailbox (calendar sync starts the workflow), or someone
   posts a meeting link in chat or email, or clicks "join now" in the web app.
2. **Schedule.** A durable timer fires at T − 60 s. Policy checks whether this employee may attend this meeting and
   in which **speak mode**.
3. **Launch the bot** from a warm pool of pre-started bot pods (join in under 5 s). The right platform adapter is
   chosen. The bot joins as the employee's own identity, with a display name marked "(AI)".
4. **Join and admit.** The bot handles lobbies and waiting rooms and retries with backoff. If it is refused, the
   organiser is notified.
5. **Announce.** A spoken and written chat disclosure on join. Transcription only runs where the consent policy
   allows it. The host can remove the bot at any time.
6. **Participate.** Live transcript, answers when addressed, meeting-chat Q&A, and reading the shared screen on
   demand. The bot heartbeats the workflow every 5 s.
7. **Leave** when the meeting ends, after being alone for 2 minutes, or when removed. Audio and transcript
   checkpoints are flushed first.
8. **Post-process.** A batch re-transcription produces the final transcript. Then come notes, decisions and action
   items. The employee's own action items become tasks for itself. Follow-ups go to the other owners. Notes go to
   attendees.

### Platform adapters
| Platform | Preferred integration | Fallback |
|---|---|---|
| Zoom | Zoom Meeting SDK for Linux (raw audio in and out) | Browser bot on the web client |
| Microsoft Teams | Graph Communications API media bot (application-hosted media; requires tenant-admin consent) | Browser guest join |
| Google Meet | Meet Media API where available to the tenant | Browser bot (headless Chromium, PulseAudio virtual mic and speaker, Xvfb) |

Adapters all implement the same internal interface (`join`, `leave`, `audio_in`, `audio_out`, `chat_in`,
`chat_out`, `roster`, `screen_frames`), so the voice pipeline does not know which platform it is on. The platform
SDKs themselves are governed by each vendor's terms. Everything on our side of the adapter is open source.
[Vexa](https://github.com/Vexa-ai/vexa) (Apache-2.0) is a useful reference implementation of open-source meeting
bots.

### Real-time voice pipeline (Pipecat)
| Stage | Component | Notes |
|---|---|---|
| Voice activity | Silero VAD | Speech segments and end-of-turn detection |
| Speech-to-text | faster-whisper (GPU), streaming | Partial and final hypotheses. Tenant vocabulary (product names, people) |
| Who is speaking | pyannote diarization, combined with the platform roster and active-speaker events | Gives named speakers, not "Speaker 2" |
| Transcript stream | NATS `meeting.transcript.*` plus an S3 checkpoint every 5 s | Durable. Also feeds the live transcript view |
| Addressing and turn-taking | Was I addressed (name, direct question, DM)? After a question, wait for 600 ms of silence. Barge-in stops speech immediately | The employee never talks over people |
| Response engine | Fast model through Model Gateway. Context: rolling summary, last 2 minutes verbatim, agenda, employee knowledge (RAG) | Anything that needs real work → "I'll follow up" → creates a task |
| Speak policy | Speak mode (listen-only, answer-when-asked, active). Only verifiable facts, with sources cited. If unsure, offer a written follow-up | External meetings default to listen-only |
| Text-to-speech | Kokoro (GPU), streaming, sentence-sized chunks, employee voice profile | Cancelled instantly on barge-in |

**Latency budget (p50 voice-to-voice ≈ 1.15 s):** end-of-turn 200 ms, STT final 250 ms, LLM first token
400 ms, TTS first audio 150 ms, transport 150 ms.

### Meeting intelligence
- **Final transcript:** WhisperX batch pass with word timestamps, attributed to speakers.
- **Notes:** summary, decisions, action items (owner and due date), open questions, risks. Every point links to a
  transcript timestamp. Long meetings are summarised with map-reduce.
- **Actions:**
  - The employee's own items become tasks via `CreateTask`.
  - Other people's items become follow-up emails or chat messages.
  - CRM and Jira updates run through the normal tool pipeline and its risk-tier policy.
- **Meeting memory:** transcripts and notes flow into the employee's Brain ([brain.md](brain.md)). Only attendees and their managers can see
  them, enforced by OpenFGA plus RLS.

### Reliability
| Failure | Handling |
|---|---|
| Bot pod crashes | The workflow's heartbeat timeout (15 s) relaunches a bot from the warm pool and rejoins. The transcript resumes from the last checkpoint |
| GPU pool saturated | STT/TTS autoscale on queue depth. Fallback to a smaller CPU model, or to chat-only answers |
| LLM slow or down | Model Gateway fallback. The employee says "let me check and follow up" and creates a task. It never blocks the conversation |
| Platform blocks the bot | Adapter fallback (official API ↔ browser). The organiser is notified. Notes are made from an uploaded recording if one exists |
| Network jitter | Jitter buffer, packet-loss concealment, reconnect with session resume |

## 3. Own laptop (Workspace)

Each employee gets **one persistent workstation**, like a company laptop:
- **Isolation:** a Kata Containers microVM (or a KubeVirt VM) gives a hardware-virtualised boundary, because the
  workspace is long-lived, has network access and runs arbitrary developer tools.
- **Inside:** a Linux desktop (Xfce) with KasmVNC/noVNC, Chromium, office apps, code-server (VS Code), git and `gh`,
  and language toolchains from `devcontainer.json`.
- **Persistent home volume** (`/home/employee`, repos, browser profile) with a nightly CSI `VolumeSnapshot`.
- **Credentials:** a git credential helper calls OpenBao for short-lived tokens. No standing secrets on disk.
- **Network:** all egress goes through the proxy with a per-employee allowlist. Package registries go through
  caching mirrors. The only inbound traffic allowed is the workspace agent and the human live view.
- **Workspace agent (daemon):** the Agent Runtime calls it over gRPC with mTLS. It supports shell exec, file
  read/write/patch, running tests, browser automation (Playwright/CDP), and screenshot plus mouse/keyboard input.
  Every call is authorized and audited.

**Control priority** (most to least reliable). The harness always uses the highest option available:
1. structured APIs and MCP tools;
2. shell and file-patch tools;
3. browser DOM (Playwright);
4. pixels: screenshot and click (computer use), only for GUI-only applications.

**Lifecycle:** `PROVISIONING → RUNNING ⇄ HIBERNATED → ARCHIVED`.
- The workspace starts when a task needs it and hibernates after 30 minutes idle.
- Resume takes under 30 seconds.
- An unhealthy workspace is rebuilt from the golden image and its volume is reattached.

**Human live view and takeover:** watch the screen live in the web app, or take control. While a human controls the
workspace, the agent pauses, and the human hands back when done. Every session is recorded for audit.

### Coding workflow
1. Task or issue arrives.
2. The employee reads the code and writes a plan.
3. It clones the repo and creates a branch.
4. It edits by patch and runs the tests and linters on its laptop.
5. It commits, signed with its own key.
6. It opens a pull request with a description and test evidence.
7. It addresses review comments.

The employee's git identity (bot user or GitHub App) **cannot push to protected branches**. CI and human review
are the gate, exactly as for a human engineer.

## 4. Digital identity

The **Identity & Accounts** service provisions every account the employee needs, through SCIM 2.0 and the
platforms' admin APIs, and removes them all automatically when the employee is archived.

| Account | How |
|---|---|
| Email and calendar | Google Workspace or Microsoft 365 mailbox, or self-hosted mail (Stalwart). Accepting an invite starts `MeetingAttendance` |
| Chat | Slack, Microsoft Teams, Google Chat, as a user or app identity depending on tenant policy. DMs and channels flow through the Channel Gateway |
| Meetings | Joins Zoom, Teams and Meet as itself. Name and avatar are marked "(AI)". Has its own voice profile |
| Code hosting | GitHub/GitLab bot user or GitHub App. Signed commits (key in OpenBao). Branch protection applies |
| Business apps | CRM, ticketing and docs through OAuth, with scopes approved by the tenant admin. Tokens are refreshed by the Integration Service |
| Workstation | Its own laptop, browser profile and files |

**Place in the organisation:**
- a manager (human or AI), a team, a role, working hours and a timezone;
- an escalation path for approvals and questions;
- a scorecard: tasks done, quality score, cost per task.

Every AI employee ultimately **reports to a human**.

## 5. Licenses (all OSI-approved)

Pipecat (BSD-2), Silero VAD (MIT), faster-whisper and WhisperX (MIT/BSD), pyannote.audio (MIT), Kokoro TTS
(Apache-2.0), Kata Containers and KubeVirt (Apache-2.0), KasmVNC (GPL-2.0), noVNC (MPL-2.0), code-server (MIT),
Playwright (Apache-2.0), Stalwart Mail (AGPL-3.0, optional). The Zoom, Teams and Meet SDKs and APIs are the
vendors' own, and they sit behind the adapter interface.
