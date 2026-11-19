# Meeting Service

> **Planned module of core-python** (spec only, not built yet). Written as the `meeting-service` service; under [ADR-0017](../../../../architecture/adr/0017-two-backend-services.md) it is built as a module inside core-python, with its own database, migrations and contracts, and no port or container of its own.

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python |
| **Database** | `meeting_db` on `pg-ledger` |

## Purpose
Joins Zoom, Microsoft Teams and Google Meet as the employee; keeps transcripts; produces notes, decisions and action items.

## Owns
- meeting attendances
- bot sessions
- transcripts
- notes
- action items

## API
- `JoinMeeting(link | calendar_event)`
- `GetTranscript` · `GetNotes`
- Temporal workflow `MeetingAttendance` (ID = platform:meeting_id:employee_id)

## Events
| Direction | Subjects |
|---|---|
| In | calendar invites (via Identity & Accounts sync) · `channel.message_received` (meeting links) |
| Out | `meeting.scheduled` · `meeting.joined` · `meeting.transcript.segment` · `meeting.left` · `meeting.notes_ready` · `meeting.action_item` |

## Depends on
- Realtime Voice
- platform adapters (Zoom SDK, Teams Graph media bot, Meet Media API, browser bot)
- Brain Service
- Task Service

## Scaling
Warm pool of bot pods; one bot per employee per meeting.

## Failure behaviour
Heartbeat timeout relaunches the bot and rejoins; transcript checkpointed to S3 every 5 s.

## Folder layout
```
meeting-service/
  api/
  workflows/
  adapters/zoom/
  adapters/teams/
  adapters/google-meet/
  adapters/browser-bot/
  intelligence/
  migrations/
  tests/
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../../../architecture/services.md)
- [digital-employee.md](../../../../architecture/digital-employee.md)
