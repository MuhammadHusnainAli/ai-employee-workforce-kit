// The Brain Inspector, feedback, proposals and rules: public shapes and the calls behind them.

import type { Backends } from "@/lib/api-client/grpc";
import { bad, type Caller, isUuid, iso, str, tenant } from "@/lib/api/http";

type Msg = Record<string, unknown>;

export const MEMORY_STATUSES = ["active", "quarantined", "rejected"] as const;
export const MEMORY_KINDS = ["episodic", "semantic", "reflection"] as const;
export const FEEDBACK_KINDS = ["correction", "rating", "approval_rejected", "comment"] as const;

const uuid = (v: string, name: string) => {
    if (!isUuid(v)) throw bad(`${name} must be a UUID`);
    return v;
};

export function memory(m: Msg) {
    return {
        id: m.memoryId,
        employee_id: m.employeeId,
        kind: m.kind,
        content: m.content,
        status: String(m.status ?? "")
            .replace("MEMORY_STATUS_", "")
            .toLowerCase(),
        trust: m.trust,
        confidence: m.confidence,
        importance: m.importance,
        fact_key: m.factKey || null,
        source_ref: m.sourceRef || null,
        valid_from: iso(m.validFrom),
        valid_to: iso(m.validTo),
        recorded_at: iso(m.recordedAt),
        superseded_by: m.supersededBy || null,
        findings: m.findings ?? {},
        contradicts: m.contradicts || null,
        reinforced: m.reinforced,
        use_count: m.useCount,
    };
}

export function proposal(p: Msg) {
    return {
        id: p.proposalId,
        employee_id: p.employeeId,
        kind: p.kind,
        text: p.text,
        risk: p.risk,
        status: p.status,
        proposed_by: p.proposedBy,
        decided_by: p.decidedBy || null,
        decision_note: p.decisionNote || null,
        feedback_id: p.feedbackId || null,
        run_id: p.runId || null,
        created_at: iso(p.createdAt),
        decided_at: iso(p.decidedAt),
    };
}

export function rule(r: Msg) {
    return {
        id: r.ruleId,
        employee_id: r.employeeId || null,
        text: r.text,
        status: r.status,
        proposal_id: r.proposalId || null,
        approved_by: r.approvedBy,
        created_at: iso(r.createdAt),
        revoked_by: r.revokedBy || null,
        revoked_at: iso(r.revokedAt),
    };
}

function list(q: URLSearchParams, name: string, allowed: readonly string[]): string[] {
    const values = q
        .getAll(name)
        .flatMap((v) => v.split(","))
        .filter(Boolean);
    for (const v of values) {
        if (!allowed.includes(v)) throw bad(`${name} must be among ${allowed.join(", ")}`);
    }
    return values;
}

export async function listMemories(b: Backends, c: Caller, employee: string, q: URLSearchParams) {
    const r = await b.brain.call<Msg>("ListMemories", {
        tenant: tenant(c),
        employeeId: employee,
        kinds: list(q, "kind", MEMORY_KINDS),
        statuses: list(q, "status", MEMORY_STATUSES).map((s) => `MEMORY_STATUS_${s.toUpperCase()}`),
        includeHistory: q.get("history") === "true",
        page: { pageSize: Number(q.get("page_size") ?? 50), pageToken: q.get("page_token") ?? "" },
    });
    return {
        memories: ((r.memories as Msg[]) ?? []).map(memory),
        next_page_token: ((r.page as Msg)?.nextPageToken as string) || null,
    };
}

export async function getMemory(b: Backends, c: Caller, id: string) {
    const r = await b.brain.call<Msg>("GetMemory", { tenant: tenant(c), memoryId: uuid(id, "id") });
    const ep = r.episode as Msg | null;
    return {
        memory: memory(r.memory as Msg),
        replaced: ((r.replaced as Msg[]) ?? []).map(memory),
        replaced_by: r.replacedBy ? memory(r.replacedBy as Msg) : null,
        episode: ep
            ? { run_id: ep.runId, task: ep.task, outcome: ep.outcome, ended_at: iso(ep.endedAt) }
            : null,
    };
}

export async function correctMemory(
    b: Backends,
    c: Caller,
    id: string,
    key: { value: string },
    body: Msg,
) {
    const r = await b.brain.call<Msg>("CorrectMemory", {
        tenant: tenant(c),
        idempotencyKey: key,
        memoryId: uuid(id, "id"),
        content: str(body.content, "content", { required: true, max: 4000 }),
    });
    return memory(r.memory as Msg);
}

export async function deleteMemory(b: Backends, c: Caller, id: string, q: URLSearchParams) {
    await b.brain.call<Msg>("DeleteMemory", {
        tenant: tenant(c),
        memoryId: uuid(id, "id"),
        reason: q.get("reason") ?? "",
    });
    return { deleted: id };
}

export async function reviewMemory(b: Backends, c: Caller, id: string, body: Msg) {
    if (typeof body.approve !== "boolean") throw bad("approve must be true or false");
    const r = await b.brain.call<Msg>("ReviewMemory", {
        tenant: tenant(c),
        memoryId: uuid(id, "id"),
        approve: body.approve,
        note: str(body.note, "note", { max: 2000 }),
    });
    return memory(r.memory as Msg);
}

export async function giveFeedback(
    b: Backends,
    c: Caller,
    employee: string,
    key: { value: string },
    body: Msg,
) {
    const kind = str(body.kind, "kind", { required: true, max: 40 });
    if (!FEEDBACK_KINDS.includes(kind as (typeof FEEDBACK_KINDS)[number])) {
        throw bad(`kind must be one of ${FEEDBACK_KINDS.join(", ")}`);
    }
    const rating = body.rating ?? 0;
    if (rating !== 0 && rating !== 1 && rating !== -1) throw bad("rating must be 1 or -1");
    const r = await b.brain.call<Msg>("Feedback", {
        tenant: tenant(c),
        idempotencyKey: key,
        employeeId: employee,
        runId: str(body.run_id, "run_id", { max: 100 }),
        kind: `FEEDBACK_KIND_${kind.toUpperCase()}`,
        text: str(body.text, "text", { max: 2000 }),
        rating,
    });
    return {
        feedback_id: r.feedbackId,
        proposal_id: r.proposalId || null,
        credited: r.credited ?? 0,
    };
}

export async function listProposals(b: Backends, c: Caller, employee: string, q: URLSearchParams) {
    const r = await b.brain.call<Msg>("ListProposals", {
        tenant: tenant(c),
        employeeId: employee,
        statuses: list(q, "status", ["pending", "approved", "rejected"]),
    });
    return { proposals: ((r.proposals as Msg[]) ?? []).map(proposal) };
}

export async function decideProposal(b: Backends, c: Caller, id: string, body: Msg) {
    if (typeof body.approve !== "boolean") throw bad("approve must be true or false");
    const r = await b.brain.call<Msg>("DecideProposal", {
        tenant: tenant(c),
        proposalId: uuid(id, "id"),
        approve: body.approve,
        note: str(body.note, "note", { max: 2000 }),
    });
    return { proposal: proposal(r.proposal as Msg), rule: r.rule ? rule(r.rule as Msg) : null };
}

export async function listRules(b: Backends, c: Caller, employee: string, q: URLSearchParams) {
    const r = await b.brain.call<Msg>("ListRules", {
        tenant: tenant(c),
        employeeId: employee,
        includeRevoked: q.get("include_revoked") === "true",
    });
    return { rules: ((r.rules as Msg[]) ?? []).map(rule) };
}

export async function revokeRule(b: Backends, c: Caller, id: string, body: Msg) {
    const r = await b.brain.call<Msg>("RevokeRule", {
        tenant: tenant(c),
        ruleId: uuid(id, "id"),
        reason: str(body.reason, "reason", { max: 1000 }),
    });
    return rule(r.rule as Msg);
}
