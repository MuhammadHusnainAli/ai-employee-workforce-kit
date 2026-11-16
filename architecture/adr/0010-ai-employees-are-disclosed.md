# ADR-0010: AI employees work like colleagues but are always disclosed as AI

**Status:** Accepted

## Context
The goal is that an AI employee is as easy to work with as a human colleague: its own accounts, calendar, laptop,
meetings and responsibilities. It is tempting to make it indistinguishable from a human. However:
- recording and transcription in meetings requires participant consent in many jurisdictions;
- AI-transparency rules (e.g. EU AI Act Art. 50) require that people are told when they interact with an AI
  system;
- customers and staff who discover a hidden AI participant stop trusting the whole product.

## Decision
- Display names, avatars and profiles are marked as AI (e.g. "Alex (AI) · Acme").
- On joining a meeting, the employee announces itself in voice and chat. Transcription follows the tenant and
  jurisdiction consent policy. Any participant can ask it to stop.
- Speak modes (listen-only, answer-when-asked, active) are set per meeting type. External meetings default to
  listen-only.
- Everything else (accounts, tools, workflow) is identical to a human colleague's.

## Consequences
- The product is sellable in regulated markets and to enterprises.
- "Seamless" is achieved through capability and quality, not concealment.
