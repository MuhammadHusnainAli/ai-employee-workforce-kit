# ADR-0001: Cut services by business capability

**Status:** Accepted

## Context
A microservices approach was requested. The common failure is splitting by entity (an "employee service", a
"prompt service", a "tool-schema service"…). Every request then fans out into chatty synchronous calls, a
transaction spans many services, and one slow service takes everything down.

## Decision
Draw a boundary only where all of these hold: separate data ownership, different scaling profile or failure
domain, and a team could own it on its own. That gives 16 cell services plus 3 global ones (see
[services.md](../services.md)). Approvals live inside Task. Scheduling lives in Temporal. Policy checks live
inside Integration, the only place actions happen.

## Consequences
- Most user requests touch one service, or two at most.
- Fewer deployables to operate, version and secure.
- If a service grows too large, it can be split later along an internal module boundary. That is cheaper than
  merging over-split services.
