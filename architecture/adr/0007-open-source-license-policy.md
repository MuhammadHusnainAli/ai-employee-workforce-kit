# ADR-0007: OSI-approved licenses only

**Status:** Accepted

## Context
Several popular infrastructure projects have moved to source-available licenses (HashiCorp Vault → BSL; Redis
7.4–7.x → RSAL/SSPL; MinIO community edition stopped being actively distributed). A kit that anyone can run must
not depend on licenses that restrict hosting or competing use.

## Decision
Use only components under OSI-approved licenses: Apache-2.0, MIT, BSD, MPL-2.0, PostgreSQL, and AGPL-3.0 for
tools that are used but not modified (Grafana, Loki, Tempo). Specific choices: OpenBao instead of Vault, Valkey
instead of Redis, SeaweedFS (S3 API) instead of MinIO. Each component sits behind a standard interface (S3, OIDC,
OTel, SQL, MCP, Gateway API).

## Consequences
- No license-change risk lock-in. Any component can be swapped for a compatible one.
- A license review (automated with SBOM plus a license scanner in CI) is part of adding any dependency.
