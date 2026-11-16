# Realtime Voice

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python (Pipecat) |
| **Database** | none (stateless) |

## Purpose
Live listening and speaking in meetings: VAD → STT → diarization → addressing / turn-taking → response engine → speak policy → TTS.

## Owns
- nothing persistent

## API
- bidirectional audio stream with the bot pod
- transcript segments → NATS `meeting.transcript.*`

## Events
| Direction | Subjects |
|---|---|
| In | audio frames from platform adapters |
| Out | `meeting.transcript.segment` |

## Depends on
- GPU inference pool (faster-whisper, pyannote, Kokoro)
- Model Gateway (fast model)
- Brain Service (`Recall`)

## Scaling
Workers next to bot pods on GPU nodes; autoscale on queue depth.

## Failure behaviour
Degrades to a smaller CPU model, then to meeting-chat answers. Latency target ≈ 1.15 s p50 voice-to-voice.

## Folder layout
```
realtime-voice/
  pipeline/
  processors/vad/
  processors/stt/
  processors/diarization/
  processors/turn-taking/
  processors/tts/
  policy/
  tests/
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../architecture/services.md)
- [digital-employee.md](../../architecture/digital-employee.md)
