"use client";
// The browser side of the public API: the same /api/v1 every customer uses.

export type TaskStatus =
    "queued" | "running" | "waiting_human" | "completed" | "failed" | "cancelled";

export interface Task {
    id: string;
    employee_id: string;
    title: string;
    input: string;
    priority: number;
    status: TaskStatus;
    stop_reason: string;
    created_by: string;
    created_at: string | null;
    updated_at: string | null;
}

export interface Run {
    id: string;
    status: string;
    stop_reason: string;
    output: string;
    steps: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: string;
}

export interface Step {
    seq: number;
    step: number;
    kind: string;
    status_after: string;
    model: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: string;
    decision: Record<string, unknown> | null;
    results: Record<string, unknown>[];
    recall_id: string | null;
    memory_degraded: boolean;
    created_at: string | null;
}

export interface Memory {
    id: string;
    employee_id: string;
    kind: "episodic" | "semantic" | "reflection";
    content: string;
    status: "active" | "quarantined" | "rejected";
    trust: "human_verified" | "system" | "observed" | "external";
    confidence: number;
    importance: number;
    fact_key: string | null;
    source_ref: string | null;
    recorded_at: string | null;
    valid_to: string | null;
    superseded_by: string | null;
    findings: Record<string, number>;
    contradicts: string | null;
    reinforced: number;
}

export interface Proposal {
    id: string;
    text: string;
    risk: "low" | "medium" | "high";
    status: "pending" | "approved" | "rejected";
    proposed_by: string;
    decided_by: string | null;
    run_id: string | null;
    created_at: string | null;
    eval_status: "pending" | "passed" | "failed";
    eval_reasons: string[];
}

export interface Rule {
    id: string;
    text: string;
    status: "active" | "revoked";
    approved_by: string;
    created_at: string | null;
    revoked_by: string | null;
}

export class ApiFailure extends Error {
    constructor(
        readonly status: number,
        message: string,
        readonly retryable: boolean,
    ) {
        super(message);
    }
}

const ACTOR_KEY = "wfk.actor";

export function currentActor(): string {
    try {
        return localStorage.getItem(ACTOR_KEY) || "maria";
    } catch {
        return "maria";
    }
}

export function setActor(name: string): void {
    try {
        localStorage.setItem(ACTOR_KEY, name);
    } catch {
        /* private mode: stays for this page only */
    }
    window.dispatchEvent(new Event("wfk-actor"));
}

/** Call /api/v1. Writes get a fresh Idempotency-Key unless the caller passes one to reuse. */
export async function api<T>(
    path: string,
    init: { method?: string; body?: unknown; key?: string } = {},
): Promise<T> {
    const headers: Record<string, string> = { "x-wfk-actor": currentActor() };
    const method = init.method ?? "GET";
    if (method !== "GET") {
        headers["content-type"] = "application/json";
        headers["idempotency-key"] = init.key ?? crypto.randomUUID();
    }
    const res = await fetch(`/api/v1${path}`, {
        method,
        headers,
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
        cache: "no-store",
    });
    const data = (await res.json().catch(() => ({}))) as {
        error?: { message: string; retryable: boolean };
    };
    if (!res.ok) {
        throw new ApiFailure(
            res.status,
            data.error?.message ?? `Request failed (${res.status})`,
            data.error?.retryable ?? false,
        );
    }
    return data as T;
}

export const shortId = (id: string) => id.slice(-6);

export function when(iso: string | null): string {
    if (!iso) return "";
    const d = new Date(iso);
    const s = Math.round((Date.now() - d.getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
