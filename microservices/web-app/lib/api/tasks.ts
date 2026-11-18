// Tasks and their runs: the public shapes, and the calls behind them. Pure over `Backends`, so
// tests use fakes. Statuses are lower case ("waiting_human"), fields snake_case.

import type { Backends } from "@/lib/api-client/grpc";
import { ApiError, bad, type Caller, isUuid, iso, str, tenant } from "@/lib/api/http";

export const TASK_STATUSES = [
    "queued",
    "running",
    "waiting_human",
    "completed",
    "failed",
    "cancelled",
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

type Msg = Record<string, unknown>;

const lower = (v: unknown, prefix: string) =>
    String(v ?? "")
        .replace(prefix, "")
        .toLowerCase();

export function task(t: Msg) {
    return {
        id: t.taskId,
        employee_id: t.employeeId,
        title: t.title,
        input: t.input,
        priority: t.priority,
        status: lower(t.status, "TASK_STATUS_") as TaskStatus,
        stop_reason: t.stopReason,
        created_by: t.createdBy,
        created_at: iso(t.createdAt),
        updated_at: iso(t.updatedAt),
    };
}

/** google.protobuf.Struct / Value as plain JSON. */
export function plain(v: unknown): unknown {
    const x = v as Msg | null | undefined;
    if (!x || typeof x !== "object") return x ?? null;
    if ("fields" in x) {
        const out: Msg = {};
        for (const [k, val] of Object.entries((x.fields as Msg) ?? {})) out[k] = plain(val);
        return out;
    }
    switch (x.kind) {
        case "nullValue":
            return null;
        case "numberValue":
            return x.numberValue;
        case "stringValue":
            return x.stringValue;
        case "boolValue":
            return x.boolValue;
        case "structValue":
            return plain(x.structValue);
        case "listValue":
            return (((x.listValue as Msg)?.values as unknown[]) ?? []).map(plain);
    }
    return null;
}

export function run(r: Msg) {
    return {
        id: r.runId,
        employee_id: r.employeeId,
        status: lower(r.status, "RUN_STATUS_"),
        stop_reason: r.stopReason,
        output: r.output,
        steps: r.steps,
        input_tokens: Number(r.inputTokens ?? 0),
        output_tokens: Number(r.outputTokens ?? 0),
        cost_usd: r.costUsd,
        created_at: iso(r.createdAt),
        updated_at: iso(r.updatedAt),
    };
}

export function step(s: Msg) {
    return {
        seq: s.seq,
        step: s.step,
        kind: s.kind,
        status_after: lower(s.statusAfter, "RUN_STATUS_"),
        model: s.model,
        input_tokens: s.inputTokens,
        output_tokens: s.outputTokens,
        cost_usd: s.costUsd,
        decision: plain(s.decision),
        results: ((s.results as unknown[]) ?? []).map(plain),
        recall_id: s.recallId || null,
        memory_degraded: s.memoryDegraded,
        created_at: iso(s.createdAt),
    };
}

function taskId(id: string): string {
    if (!isUuid(id)) throw bad("task id must be a UUID");
    return id;
}

const one = (r: Msg) => task(r.task as Msg);

export async function listTasks(b: Backends, c: Caller, q: URLSearchParams) {
    const status = q.get("status") ?? "";
    if (status && !TASK_STATUSES.includes(status as TaskStatus)) {
        throw bad(`status must be one of ${TASK_STATUSES.join(", ")}`);
    }
    const limit = Number(q.get("limit") ?? 50);
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw bad("limit must be 1 to 200");
    const r = await b.tasks.call<Msg>("ListTasks", {
        tenant: tenant(c),
        employeeId: q.get("employee_id") ?? "",
        status: status ? `TASK_STATUS_${status.toUpperCase()}` : "TASK_STATUS_UNSPECIFIED",
        limit,
    });
    return { tasks: ((r.tasks as Msg[]) ?? []).map(task) };
}

export async function createTask(b: Backends, c: Caller, key: { value: string }, body: Msg) {
    const priority = body.priority ?? 0;
    if (
        typeof priority !== "number" ||
        !Number.isInteger(priority) ||
        priority < 0 ||
        priority > 9
    ) {
        throw bad("priority must be an integer from 0 to 9");
    }
    const r = await b.tasks.call<Msg>("CreateTask", {
        tenant: tenant(c),
        idempotencyKey: key,
        employeeId: str(body.employee_id, "employee_id", { required: true, max: 200 }),
        title: str(body.title, "title", { max: 300 }),
        input: str(body.input, "input", { required: true }),
        priority,
    });
    return one(r);
}

/** The task and, once it has started, its run. */
export async function getTask(b: Backends, c: Caller, id: string) {
    const t = one(await b.tasks.call<Msg>("GetTask", { tenant: tenant(c), taskId: taskId(id) }));
    let r: ReturnType<typeof run> | null = null;
    try {
        const got = await b.runs.call<Msg>("GetRun", { tenant: tenant(c), runId: id });
        r = run(got.run as Msg);
    } catch (err) {
        const code = (err as { code?: number }).code;
        if (code !== 5) throw err; // NOT_FOUND: the run has not started yet
    }
    return { task: t, run: r };
}

export async function listSteps(b: Backends, c: Caller, id: string, q: URLSearchParams) {
    const r = await b.runs.call<Msg>("ListSteps", {
        tenant: tenant(c),
        runId: taskId(id),
        page: { pageSize: Number(q.get("page_size") ?? 50), pageToken: q.get("page_token") ?? "" },
    });
    return {
        steps: ((r.steps as Msg[]) ?? []).map(step),
        next_page_token: ((r.page as Msg)?.nextPageToken as string) || null,
    };
}

export async function cancelTask(b: Backends, c: Caller, id: string, body: Msg) {
    return one(
        await b.tasks.call<Msg>("CancelTask", {
            tenant: tenant(c),
            taskId: taskId(id),
            reason: str(body.reason, "reason", { max: 1000 }),
        }),
    );
}

export async function decideApproval(
    b: Backends,
    c: Caller,
    id: string,
    key: { value: string },
    body: Msg,
) {
    if (typeof body.approved !== "boolean") throw bad("approved must be true or false");
    return one(
        await b.tasks.call<Msg>("DecideApproval", {
            tenant: tenant(c),
            idempotencyKey: key,
            taskId: taskId(id),
            approved: body.approved,
            note: str(body.note, "note", { max: 2000 }),
        }),
    );
}

export async function replyToTask(
    b: Backends,
    c: Caller,
    id: string,
    key: { value: string },
    body: Msg,
) {
    return one(
        await b.tasks.call<Msg>("ReplyToTask", {
            tenant: tenant(c),
            idempotencyKey: key,
            taskId: taskId(id),
            text: str(body.text, "text", { required: true, max: 20_000 }),
        }),
    );
}

export function assertFound<T>(v: T | undefined, what: string): T {
    if (v === undefined) throw new ApiError(404, "not_found", `no ${what}`);
    return v;
}
