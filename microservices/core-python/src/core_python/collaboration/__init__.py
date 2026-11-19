"""collaboration module: conversations between AI employees and with the person.

Direct messages, calls (a short question and answer exchange) and standups run by the co-CEO.
Every message is kept, so the person can read all of them.

Layout:
- `api`: transport. gRPC CollaborationService.
- `domain`: rules and prompts. No I/O.
- `adapters`: Postgres (collab_db), the Model Gateway, other modules over gRPC.
"""
