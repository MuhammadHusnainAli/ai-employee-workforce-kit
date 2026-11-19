// The API handlers over fake backends: what they send, what they return, how errors look.
import { afterEach, describe, expect, it, vi } from "vitest";
import { status as Code } from "@grpc/grpc-js";
import type { Backends, Service } from "@/lib/api-client/grpc";
import * as http from "@/lib/api/http";
import * as tasks from "@/lib/api/tasks";
import * as brain from "@/lib/api/brain";
import * as workforce from "@/lib/api/workforce";
import { toSse } from "@/lib/api/events";

const TENANT = "0193a7c4-0000-7000-8000-00000000d3e0";
const TASK = "01a1ed2a-e4bd-7110-8031-aa3e1601f3c9";
const caller: http.Caller = { tenantId: TENANT, actorId: "maria" };
const key = { value: "k-1" };

type Call = { method: string; request: Record<string, unknown> };

function fake(answers: Record<string, unknown | Error> = {}): Service & { calls: Call[] } {
    const calls: Call[] = [];
    return {
        calls,
        call<T>(method: string, request: object): Promise<T> {
            calls.push({ method, request: request as Record<string, unknown> });
            const a = answers[method];
            return a instanceof Error ? Promise.reject(a) : Promise.resolve((a ?? {}) as T);
        },
    };
}

const grpcError = (code: number, details: string) =>
    Object.assign(new Error(details), { code, details });

const taskMsg = {
    taskId: TASK,
    employeeId: "summariser",
    title: "t",
    input: "i",
    priority: 0,
    status: "TASK_STATUS_WAITING_HUMAN",
    stopReason: "approval",
    createdBy: "maria",
    createdAt: { seconds: "1794981917", nanos: 445041000 },
    updatedAt: null,
};

function backends(over: Partial<Record<keyof Backends, Service>> = {}): Backends {
    return {
        tasks: fake(),
        runs: fake(),
        brain: fake(),
        workforce: fake(),
        collaboration: fake(),
        ...over,
    };
}

afterEach(() => vi.unstubAllEnvs());

describe("tasks", () => {
    it("creates a task as the person, with the idempotency key", async () => {
        const t = fake({ CreateTask: { task: taskMsg } });
        const out = await tasks.createTask(backends({ tasks: t }), caller, key, {
            employee_id: "summariser",
            input: "Summarise the deck",
        });
        expect(t.calls[0]).toEqual({
            method: "CreateTask",
            request: {
                tenant: { tenantId: TENANT, actor: { kind: "ACTOR_KIND_HUMAN", id: "maria" } },
                idempotencyKey: key,
                employeeId: "summariser",
                title: "",
                input: "Summarise the deck",
                priority: 0,
            },
        });
        expect(out).toMatchObject({
            id: TASK,
            status: "waiting_human",
            created_at: "2026-11-18T06:05:17.445Z",
        });
    });

    it("refuses bad input before calling anything", async () => {
        const t = fake();
        const b = backends({ tasks: t });
        await expect(tasks.createTask(b, caller, key, { input: "x" })).rejects.toThrow(
            "employee_id",
        );
        await expect(
            tasks.createTask(b, caller, key, { employee_id: "e", input: "x", priority: 10 }),
        ).rejects.toThrow("priority");
        await expect(
            tasks.listTasks(b, caller, new URLSearchParams("status=done")),
        ).rejects.toThrow("status");
        await expect(tasks.getTask(b, caller, "not-a-uuid")).rejects.toThrow("UUID");
        expect(t.calls).toEqual([]);
    });

    it("filters by status in the backend's terms", async () => {
        const t = fake({ ListTasks: { tasks: [taskMsg] } });
        const out = await tasks.listTasks(
            backends({ tasks: t }),
            caller,
            new URLSearchParams("status=waiting_human"),
        );
        expect(t.calls[0]!.request.status).toBe("TASK_STATUS_WAITING_HUMAN");
        expect(out.tasks).toHaveLength(1);
    });

    it("returns the task without a run until the run starts", async () => {
        const runs = fake({ GetRun: grpcError(Code.NOT_FOUND, "no run") });
        const out = await tasks.getTask(
            backends({ tasks: fake({ GetTask: { task: taskMsg } }), runs }),
            caller,
            TASK,
        );
        expect(out.run).toBeNull();
        const down = fake({ GetRun: grpcError(Code.UNAVAILABLE, "down") });
        await expect(
            tasks.getTask(
                backends({ tasks: fake({ GetTask: { task: taskMsg } }), runs: down }),
                caller,
                TASK,
            ),
        ).rejects.toMatchObject({ code: Code.UNAVAILABLE });
    });

    it("turns protobuf Structs in steps into plain JSON", () => {
        const s = tasks.step({
            seq: 1,
            decision: {
                fields: {
                    kind: { kind: "stringValue", stringValue: "tool_calls" },
                    calls: {
                        kind: "listValue",
                        listValue: { values: [{ kind: "numberValue", numberValue: 2 }] },
                    },
                    ok: { kind: "boolValue", boolValue: true },
                    none: { kind: "nullValue", nullValue: "NULL_VALUE" },
                },
            },
            results: [],
            statusAfter: "RUN_STATUS_COMPLETED",
        });
        expect(s.decision).toEqual({ kind: "tool_calls", calls: [2], ok: true, none: null });
        expect(s.status_after).toBe("completed");
    });
});

describe("brain", () => {
    it("sends feedback kinds and statuses as the contract's enums", async () => {
        const b = fake({ Feedback: { feedbackId: "f", proposalId: "p", credited: 0 } });
        const out = await brain.giveFeedback(backends({ brain: b }), caller, "summariser", key, {
            kind: "correction",
            text: "Never CC finance.",
            run_id: TASK,
        });
        expect(b.calls[0]!.request).toMatchObject({
            kind: "FEEDBACK_KIND_CORRECTION",
            runId: TASK,
        });
        expect(out).toEqual({ feedback_id: "f", proposal_id: "p", credited: 0 });
        await expect(
            brain.giveFeedback(backends(), caller, "e", key, { kind: "praise" }),
        ).rejects.toThrow("kind");
        const m = fake({ ListMemories: { memories: [], page: { nextPageToken: "" } } });
        await brain.listMemories(
            backends({ brain: m }),
            caller,
            "e",
            new URLSearchParams("status=quarantined"),
        );
        expect(m.calls[0]!.request.statuses).toEqual(["MEMORY_STATUS_QUARANTINED"]);
        await expect(
            brain.listMemories(backends(), caller, "e", new URLSearchParams("status=lost")),
        ).rejects.toThrow("status");
    });
});

describe("http", () => {
    it("maps gRPC codes to statuses with retry guidance", async () => {
        const res = await http.handle(() =>
            Promise.reject(grpcError(Code.UNAVAILABLE, "restarting")),
        );
        expect(res.status).toBe(503);
        expect(res.headers.get("retry-after")).toBe("2");
        expect(await res.json()).toEqual({
            error: { code: "unavailable", message: "restarting", retryable: true },
        });
        const cases: [number, number][] = [
            [Code.INVALID_ARGUMENT, 400],
            [Code.NOT_FOUND, 404],
            [Code.ALREADY_EXISTS, 409],
            [Code.FAILED_PRECONDITION, 409],
            [Code.PERMISSION_DENIED, 403],
            [Code.DEADLINE_EXCEEDED, 504],
        ];
        for (const [code, status] of cases) {
            expect(http.fromGrpc(grpcError(code, "x")).status).toBe(status);
        }
        expect(http.fromGrpc(new Error("boom")).status).toBe(500);
    });

    it("needs an idempotency key and a token when one is configured", () => {
        expect(() => http.idempotencyKey(new Request("http://x", { method: "POST" }))).toThrow(
            "Idempotency-Key",
        );
        vi.stubEnv("WFK_API_TOKEN", "t0ken");
        expect(() => http.caller(new Request("http://x"))).toThrow("bearer");
        const ok = http.caller(
            new Request("http://x", {
                headers: { authorization: "Bearer t0ken", "x-wfk-actor": "omar" },
            }),
        );
        expect(ok.actorId).toBe("omar");
    });

    it("checks employee ids from the path", () => {
        expect(http.employee("summariser")).toBe("summariser");
        expect(() => http.employee("../etc")).toThrow();
    });
});

describe("the live stream", () => {
    const event = (over: Record<string, unknown> = {}) =>
        JSON.stringify({
            id: "e1",
            type: "task.state_changed",
            tenantid: TENANT,
            subject: TASK,
            time: "2026-11-18T06:00:00Z",
            data: { task_id: TASK, to: "completed", stop_reason: "final" },
            ...over,
        });

    it("sends only this tenant's task events", () => {
        const out = toSse(event(), TENANT);
        expect(out).toContain("event: task\n");
        expect(JSON.parse(out!.split("data: ")[1]!)).toEqual({
            type: "task.state_changed",
            task_id: TASK,
            status: "completed",
            stop_reason: "final",
            at: "2026-11-18T06:00:00Z",
        });
        expect(toSse(event({ tenantid: "someone-else" }), TENANT)).toBeNull();
        expect(toSse(event({ type: "run.completed" }), TENANT)).toBeNull();
        expect(toSse("not json", TENANT)).toBeNull();
    });
});

describe("employees", () => {
    const answers = {
        CreateEmployee: { employee: { employeeId: "ana-support" } },
        PublishVersion: { version: { version: 1 } },
        GetEmployee: {
            employee: {
                employeeId: "ana-support",
                name: "Ana",
                status: "EMPLOYEE_STATUS_ACTIVE",
                activeVersion: 1,
            },
        },
        GetVersion: {
            version: {
                version: 1,
                role: "Support",
                model: "claude-haiku",
                toolGrants: [{ tool: "word_count", tier: "RISK_TIER_R0" }],
                limits: { maxSteps: 8, maxCostUsd: "0.50" },
            },
        },
    };

    it("creates the employee and publishes its first version", async () => {
        const w = fake(answers);
        const out = await workforce.createEmployee(backends({ workforce: w }), caller, key, {
            id: "ana-support",
            name: "Ana",
            role: "Support",
            instructions: "Be kind.",
            model: "claude-haiku",
            tools: [{ tool: "word_count", tier: "R0" }],
            max_steps: 8,
            max_cost_usd: "0.50",
        });
        expect(w.calls.map((c) => c.method)).toEqual([
            "CreateEmployee",
            "PublishVersion",
            "GetEmployee",
            "GetVersion",
        ]);
        expect(w.calls[1]!.request).toMatchObject({
            employeeId: "ana-support",
            idempotencyKey: key,
            toolGrants: [{ tool: "word_count", tier: "RISK_TIER_R0" }],
            limits: { maxSteps: 8, maxCostUsd: "0.50" },
        });
        expect(out.employee).toMatchObject({
            id: "ana-support",
            status: "active",
            active_version: 1,
        });
        expect(out.active_version).toMatchObject({ tools: [{ tool: "word_count", tier: "R0" }] });
    });

    it("refuses bad input before creating anything", async () => {
        const w = fake(answers);
        const b = backends({ workforce: w });
        const good = { id: "ana", name: "Ana", role: "r", model: "m" };
        await expect(
            workforce.createEmployee(b, caller, key, { ...good, id: "Ana Support" }),
        ).rejects.toThrow("id");
        await expect(
            workforce.createEmployee(b, caller, key, {
                ...good,
                tools: [{ tool: "x", tier: "R9" }],
            }),
        ).rejects.toThrow("tier");
        await expect(
            workforce.createEmployee(b, caller, key, { ...good, max_cost_usd: "lots" }),
        ).rejects.toThrow("max_cost_usd");
        await expect(
            workforce.createEmployee(b, caller, key, { ...good, role: "" }),
        ).rejects.toThrow("role");
        expect(w.calls).toEqual([]);
    });
});
