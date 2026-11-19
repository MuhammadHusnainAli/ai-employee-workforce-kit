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
- `StartStandup` (the whole company or one department) · `GetStandup` · `ListStandups` ·
  `EndStandupDiscussion`

Who speaks comes from the tenant context: actor kind `EMPLOYEE` speaks as that employee, anyone else as the person.

## Events
| Direction | Stream | Subjects |
|---|---|---|
| Out | `COLLAB` | `collab.message_posted` · `collab.standup_started` · `collab.standup_end_requested` · `collab.standup_finished` |
| In | `COLLAB` (durable consumer `collaboration-standups`) | `collab.standup_started` starts the `Standup` workflow; a person's `collab.message_posted` in a standup and `collab.standup_end_requested` signal it |

## Standups
`Standup` is a Temporal workflow on queue `collab` (workflow id `standup-<standup id>`). A standup is also a
conversation with the same id, so its updates, discussion and summary read like any other conversation.

1. Roster from the org chart: the co-CEO leads; the whole company or one department reports. No co-CEO: the
   standup fails and says so.
2. Updates from real task status (`ListTasks` in core-rust), five at a time. Someone with nothing new since the
   last standup of the same scope gets a fixed line and costs no model call.
3. Discussion: the person posts into the conversation; each post gets one answer from the co-CEO. It closes after
   `discussion_s` seconds of quiet (default 120, 0 skips it) or when the person ends it.
4. Summary by the co-CEO: where we are, where we go, at most 10 next steps with an owner (an employee or
   `person`), risks. With `assign_next_steps`, each employee's step becomes a task, once per standup and step.

## Depends on
- workforce module (`GetEmployee`, `GetVersion`): who a colleague is
- Model Gateway: colleagues' answers, standup updates and summaries
- task module of core-rust (`ListTasks`, `CreateTask`): what everyone did; next steps as tasks
- Temporal: the `Standup` workflow

## Failure behaviour
A message and its event commit together. If the model call of a call turn fails, the question stays stored and
the same key asks again. A closed conversation takes nothing more (`FAILED_PRECONDITION`).

## Folder layout (inside core-python)
```
core-python/
  contracts/proto/wfk/collaboration_service/v1/
  migrations/collaboration/      0001 outbox/inbox, 0002 conversations, 0003 standups
  src/core_python/collaboration/
    api/                         CollaborationService gRPC
    domain/                      pair keys, caps, the call prompt; standup facts, prompts, parsing
    standup/                     the Standup workflow, its activities and inputs
    worker.py                    the Standup worker and the collab.* inbox
    adapters/                    collab_db, the call answerer, colleagues from workforce
  tests/collaboration/
```
