# ADR-0009: One persistent microVM workspace ("own laptop") per employee

**Status:** Accepted

## Context
Real work, and coding especially, needs a persistent environment: cloned repos, installed toolchains, browser
sessions and files that survive between tasks. Ephemeral sandboxes are right for untrusted snippets but wrong for a
colleague's day-to-day work. Containers alone are a weak boundary for long-lived environments that run arbitrary
tools and have network access.

## Decision
- Each employee gets a **Kata Containers microVM** (KubeVirt as an alternative) with a persistent home volume,
  nightly snapshots, a desktop, an IDE and toolchains, managed by the Workspace Manager.
- The agent controls it through an in-VM **workspace agent** over mTLS. It prefers structured tools, then shell and
  files, then browser DOM, and uses pixel-level computer use only for GUI-only apps.
- Egress goes through the proxy allowlist. Credentials are short-lived from OpenBao. Humans can watch live and take
  over.
- Untrusted or generated code still runs in the separate ephemeral **gVisor sandbox**.

## Consequences
- The employee keeps context across tasks, like a real laptop.
- The cost is a VM per active employee. Hibernation after 30 minutes idle and resume in under 30 s keep it
  manageable.
- Corruption is recoverable: rebuild from the golden image and restore the volume snapshot.
