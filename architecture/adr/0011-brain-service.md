# ADR-0011: A dedicated Brain Service with bi-temporal memory in PostgreSQL

**Status:** Accepted (supersedes the "Knowledge & Memory" service of the first draft)

## Context
Each AI employee needs persistent identity, memory, skills and preferences that survive model changes and improve
over time. A plain "vector store of chunks" cannot answer "what is true *now*", cannot explain why the employee did
something, cannot be rolled back, and is easy to poison.

## Decision
- One **Brain Service** owns all employee cognition: identity core, episodic, semantic graph, procedural (skills),
  preferences and rules, reflections. Tenant documents are its tenant-scope knowledge.
- Storage is one database `brain_db` on its own CloudNativePG cluster `pg-brain`. It combines relational tables,
  pgvector, tsvector, and a graph as adjacency tables with recursive CTEs. Facts are **bi-temporal** and superseded,
  never overwritten.
- **Recall** (synchronous, p95 under 300 ms, degrades gracefully) is separated from **Remember** (asynchronous,
  idempotent, governed). They run as four deployables from one codebase.
- Every recall is logged (`recall_log`) for citations and credit assignment.

## Consequences
- The model can be swapped without losing a colleague. The brain's state at any time T is reproducible.
- One transaction keeps facts, entities, embeddings and events consistent.
- At very large scale, pgvector or CTE limits may justify Apache AGE or a vector engine. That change sits behind the
  `Recall` API.
