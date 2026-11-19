// Conversations between employees and with the person (direct messages, calls, standups), and
// standups run by the co-CEO. The person can read every conversation and post into any of them.

import type { Backends } from "@/lib/api-client/grpc";
import { bad, type Caller, isUuid, iso, str, tenant } from "@/lib/api/http";

type Msg = Record<string, unknown>;

export const KINDS = ["direct", "call", "standup"] as const;
const lower = (v: unknown, prefix: string) =>
    String(v ?? "")
        .replace(prefix, "")
        .toLowerCase();

function uuid(v: string, name: string): string {
    if (!isUuid(v)) throw bad(`${name} must be a UUID`);
    return v;
}

export function message(m: Msg) {
    return {
        seq: m.seq as number,
        author: m.author as string,
        author_kind: m.authorKind as string,
        kind: m.kind as string,
        text: m.text as string,
        created_at: iso(m.createdAt),
    };
}

export function conversation(c: Msg) {
    const last = c.lastMessage as Msg | null | undefined;
    return {
        id: c.conversationId as string,
        kind: lower(c.kind, "CONVERSATION_KIND_"),
        topic: (c.topic as string) ?? "",
        status: (c.status as string) ?? "",
        started_by: (c.startedBy as string) ?? "",
        participants: ((c.participants as Msg[]) ?? []).map((p) => ({
            id: p.id as string,
            kind: p.kind as string,
        })),
        turns: (c.turns as number) ?? 0,
        max_turns: (c.maxTurns as number) ?? 0,
        last_seq: (c.lastSeq as number) ?? 0,
        last_message: (last?.text as string) ?? null,
        last_author: (last?.author as string) ?? null,
        updated_at: iso(c.updatedAt),
    };
}

export function standup(s: Msg) {
    return {
        id: s.standupId as string,
        department_id: (s.departmentId as string) || null,
        status: s.status as string,
        led_by: (s.ledBy as string) || null,
        started_by: s.startedBy as string,
        discussion_s: (s.discussionS as number) ?? 0,
        assign_next_steps: Boolean(s.assignNextSteps),
        reports: ((s.reports as Msg[]) ?? []).map((r) => ({
            employee_id: r.employeeId as string,
            title: r.title as string,
            done: r.done as string,
            next: r.next as string,
            blockers: r.blockers as string,
            idle: Boolean(r.idle),
        })),
        where_we_are: (s.whereWeAre as string) ?? "",
        where_we_go: (s.whereWeGo as string) ?? "",
        next_steps: ((s.nextSteps as Msg[]) ?? []).map((n) => ({
            owner: n.owner as string,
            action: n.action as string,
            task_id: (n.taskId as string) || null,
        })),
        risks: (s.risks as string[]) ?? [],
        error: (s.error as string) || null,
        created_at: iso(s.createdAt),
        finished_at: iso(s.finishedAt),
    };
}

export async function listConversations(b: Backends, c: Caller, q: URLSearchParams) {
    const kind = q.get("kind") ?? "";
    if (kind && !KINDS.includes(kind as (typeof KINDS)[number])) {
        throw bad(`kind must be one of ${KINDS.join(", ")}`);
    }
    const limit = Number(q.get("limit") ?? 50);
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw bad("limit must be 1 to 200");
    const r = await b.collaboration.call<Msg>("ListConversations", {
        tenant: tenant(c),
        participant: q.get("participant") ?? "",
        kind: kind ? `CONVERSATION_KIND_${kind.toUpperCase()}` : "CONVERSATION_KIND_UNSPECIFIED",
        limit,
    });
    return { conversations: ((r.conversations as Msg[]) ?? []).map(conversation) };
}

export async function getConversation(b: Backends, c: Caller, id: string, q: URLSearchParams) {
    const after = Number(q.get("after") ?? 0);
    if (!Number.isInteger(after) || after < 0) throw bad("after must be a whole number");
    const r = await b.collaboration.call<Msg>("GetConversation", {
        tenant: tenant(c),
        conversationId: str(id, "id", { required: true, max: 100 }),
        afterSeq: after,
        limit: 500,
    });
    return {
        conversation: conversation(r.conversation as Msg),
        messages: ((r.messages as Msg[]) ?? []).map(message),
    };
}

/** The person says something in a conversation (joins a call, speaks in a standup). */
export async function postMessage(
    b: Backends,
    c: Caller,
    key: { value: string },
    id: string,
    body: Msg,
) {
    const r = await b.collaboration.call<Msg>("PostMessage", {
        tenant: tenant(c),
        idempotencyKey: key,
        conversationId: str(id, "id", { required: true, max: 100 }),
        text: str(body.text, "text", { required: true, max: 20_000 }),
    });
    return message(r.message as Msg);
}

/** The person messages an employee directly. */
export async function sendMessage(b: Backends, c: Caller, key: { value: string }, body: Msg) {
    const r = await b.collaboration.call<Msg>("SendMessage", {
        tenant: tenant(c),
        idempotencyKey: key,
        to: str(body.to, "to", { required: true, max: 63 }),
        text: str(body.text, "text", { required: true, max: 20_000 }),
    });
    return message(r.message as Msg);
}

export async function listStandups(b: Backends, c: Caller) {
    const r = await b.collaboration.call<Msg>("ListStandups", { tenant: tenant(c), limit: 50 });
    return { standups: ((r.standups as Msg[]) ?? []).map(standup) };
}

export async function startStandup(b: Backends, c: Caller, key: { value: string }, body: Msg) {
    const seconds = body.discussion_s ?? 120;
    if (
        typeof seconds !== "number" ||
        !Number.isInteger(seconds) ||
        seconds < 0 ||
        seconds > 3600
    ) {
        throw bad("discussion_s must be a whole number of seconds from 0 to 3600");
    }
    const r = await b.collaboration.call<Msg>("StartStandup", {
        tenant: tenant(c),
        idempotencyKey: key,
        departmentId: str(body.department_id, "department_id", { max: 63 }),
        discussionS: seconds,
        assignNextSteps: Boolean(body.assign_next_steps),
    });
    return standup(r.standup as Msg);
}

export async function getStandup(b: Backends, c: Caller, id: string) {
    const r = await b.collaboration.call<Msg>("GetStandup", {
        tenant: tenant(c),
        standupId: uuid(id, "id"),
    });
    return standup(r.standup as Msg);
}

export async function endStandup(b: Backends, c: Caller, id: string) {
    const r = await b.collaboration.call<Msg>("EndStandupDiscussion", {
        tenant: tenant(c),
        standupId: uuid(id, "id"),
    });
    return standup(r.standup as Msg);
}
