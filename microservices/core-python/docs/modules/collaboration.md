# Collaboration

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python |
| **Database** | `collab_db` on `pg-app` |

## Purpose
Conversations between AI employees and with the person who owns the workspace: direct messages, calls and
standups run by the co-CEO. Every message is kept, so the person can read every conversation.

## Owns
- `conversations`: `direct` (one per pair, reused), `call` (at most 6 questions, then closed), `standup`
- `participants`: employees, and `person` for the person; each with the last message it has read
- `messages`: in order per conversation (`seq`), idempotent on the caller's key

## API (gRPC, `contracts/proto/wfk/collaboration_service/v1/collaboration.proto`)
- `SendMessage` (to an employee or `person`) · `PostMessage` (the person may join any conversation; an employee
  only its own) · `ListConversations` · `GetConversation` (messages after a sequence, for live reading)
- `ReadInbox`: unread direct messages for the calling employee, marked read
- `AskColleague`: one call turn. The question is stored, the colleague answers in role (one model call through
  the Model Gateway, no tools, under 150 words) and the answer is stored. A retry with the same key returns the
  stored answer without a second model call.

Who speaks comes from the tenant context: actor kind `EMPLOYEE` speaks as that employee, anyone else as the person.

## Events
| Direction | Stream | Subjects |
|---|---|---|
| Out | `COLLAB` | `collab.message_posted` |

## Depends on
- workforce module (`GetEmployee`, `GetVersion`): who a colleague is
- Model Gateway: colleagues' answers

## Failure behaviour
A message and its event commit together. If the model call of a call turn fails, the question stays stored and
the same key asks again. A closed conversation takes nothing more (`FAILED_PRECONDITION`).

## Folder layout (inside core-python)
```
core-python/
  contracts/proto/wfk/collaboration_service/v1/
  migrations/collaboration/      0001 outbox/inbox, 0002 conversations
  src/core_python/collaboration/
    api/                         CollaborationService gRPC
    domain/                      pair keys, caps, the call prompt
    adapters/                    collab_db, the call answerer, colleagues from workforce
  tests/collaboration/
```
