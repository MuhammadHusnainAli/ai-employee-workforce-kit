# ADR-0018: Remove the service templates and `just new-service`

**Status:** Accepted (supersedes the templates consequence of [ADR-0017](0017-two-backend-services.md) and the
templates part of [ADR-0015](0015-self-contained-microservices.md))

## Context
[ADR-0017](0017-two-backend-services.md) made the backend two services, one per language, and kept the Python and
Rust templates and `just new-service` for one case: splitting a module out into its own service later. That case
has not happened, and the templates are a third and fourth copy of the platform code (91 files) that must be kept
in step with `core-python` and `core-rust` for a use nobody has yet.

## Decision
- Delete `microservices/templates/` (both templates, the generator and its tests) and the `just new-service`
  recipe.
- New capabilities are modules in `core-python` or `core-rust` (ADR-0017).
- If a module ever has to become its own service, start from a copy of its current service's project and remove the
  other modules. That copy has the platform code as it is then, not as a template last left it.

## Consequences
- One copy of the platform code per language, with nothing else to keep in step.
- Splitting a module out is a manual copy and trim instead of a generator run. It is expected to be rare.
- Port 7016, the templates' default, is free like the rest of 7003 to 7016.
