# AI Employee Workforce Kit

An open-source kit for creating **AI employees**: digital workers with their own role, brain, accounts and laptop
that take tasks, join meetings, write code and improve over time, under human control.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Status: architecture](https://img.shields.io/badge/status-architecture%20%26%20design-orange)

## What an AI employee can do

- Work through tasks from the web app, API, email, Slack, Teams, Google Chat or a schedule
- Join **Zoom, Microsoft Teams and Google Meet** calls, answer questions by voice, take notes and create follow-ups
- Write code on **its own laptop** (a persistent VM) and open pull requests for human review
- Use **its own accounts**: email, calendar, chat, git and business apps
- **Remember and learn** through a per-employee brain, with every behaviour change tested before it goes live
- Always identify itself as AI, and ask a human before risky actions

## Architecture at a glance

- **Microservices:** 16 per-cell services plus 2 global ones, each owning one database
- **PostgreSQL** is the only system of record (CloudNativePG, sync replication, point-in-time recovery)
- **Temporal** makes every agent run durable: a crash resumes from the last step, never repeats an action
- **Cell-based isolation:** one failing cell or noisy tenant never affects the others
- **100 % OSI-licensed stack:** Temporal, NATS JetStream, Keycloak, OpenFGA, OpenBao, LiteLLM, Pipecat, Kata Containers and more

Open [architecture/diagrams/ai-workforce-architecture.drawio](architecture/diagrams/ai-workforce-architecture.drawio)
in [diagrams.net](https://app.diagrams.net) for the 12-page diagram.

## Repository layout

```
architecture/     design docs, ADRs and the draw.io diagram
  README.md         start here: principles, tech choices, page index
  brain.md          the per-employee brain (memory, recall, learning)
  agent-harness.md  the agent loop in depth
  adr/              architecture decision records
microservices/    one folder per service, each with its spec (README.md) and layout
LICENSE           Apache License 2.0
```

## Status

This repository currently contains the **architecture and service specifications**. Implementation follows the build
order in [architecture/brain.md](architecture/brain.md) and the service specs in
[microservices/](microservices/README.md).

## Author

| | |
|---|---|
| **Author** | Muhammad Husnain Ali ([@MuhammadHusnainAli](https://github.com/MuhammadHusnainAli)) |
| **Created** | 16 November 2026 |
| **Role** | Architecture and design |

## Contributing

Issues and pull requests are welcome. For significant changes, open an issue first and, where it changes the design,
add an ADR under [architecture/adr/](architecture/adr/README.md).

## License

Copyright © 2026 Muhammad Husnain Ali.
Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
