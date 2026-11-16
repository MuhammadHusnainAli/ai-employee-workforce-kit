# ADR-0008: Self-hosted streaming voice pipeline (Pipecat) behind platform adapters

**Status:** Accepted

## Context
AI employees must join Zoom, Teams and Google Meet, understand who said what, and answer by voice within about a
second, with no talking over people. Each meeting platform exposes media differently: SDK, Graph media bot, Media
API, or only the web client.

## Decision
- One internal **adapter interface** (`join`, `leave`, `audio_in/out`, `chat_in/out`, `roster`, `screen_frames`)
  per platform. The official SDK or API is preferred, with a browser bot as fallback.
- A **Pipecat** streaming pipeline (VAD → STT → diarization → turn-taking → LLM → speak policy → TTS) runs next to
  the bot. STT, TTS and diarization models are **self-hosted on a GPU pool** (faster-whisper, Kokoro, pyannote), so
  audio never leaves the cell.
- A `MeetingAttendance` Temporal workflow supervises each bot with heartbeats. Transcripts are checkpointed to S3
  every 5 s.

## Consequences
- The voice pipeline is platform-agnostic. A new platform needs only a new adapter.
- Latency target ≈ 1.15 s p50 voice-to-voice. It needs GPU capacity planning and a degrade path (smaller CPU
  model, then chat-only).
- Bot crashes cost seconds of audio, not the meeting.
