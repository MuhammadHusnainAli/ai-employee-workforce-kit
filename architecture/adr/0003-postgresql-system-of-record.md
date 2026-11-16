# ADR-0003: PostgreSQL as the only system of record

**Status:** Accepted

## Context
The platform needs relational OLTP data, flexible JSON, full-text search, vector similarity, append-only ledgers
and reliable queues. Using a specialised engine for each need (MongoDB, Elasticsearch, Pinecone/Qdrant, Kafka for
state) multiplies operational load, backup strategies and consistency problems.

## Decision
PostgreSQL 18 is the only durable store for business state, with pgvector, built-in FTS, JSONB and pg_partman.
It runs on Kubernetes with CloudNativePG: 3 instances across AZs, synchronous quorum replication, PgBouncer
poolers, Barman WAL archiving with cross-region PITR. Clusters are grouped by workload shape (app, ledger,
brain, temporal). Each service owns one database and one role.

## Consequences
- One engine to master, monitor, back up and restore. Embeddings commit in the same transaction as their source.
- Pgvector's limits (roughly tens of millions of vectors per cluster with HNSW) are handled per cell. A dedicated
  vector engine is a later, measured decision with a clear swap point (the Brain Service `Recall` API).
- Valkey and S3 are explicitly non-authoritative.
