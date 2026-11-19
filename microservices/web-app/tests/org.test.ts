// The organisation, conversation and standup handlers over fake backends.
import { describe, expect, it } from "vitest";
import { status as Code } from "@grpc/grpc-js";
import type { Backends, Service } from "@/lib/api-client/grpc";
import * as http from "@/lib/api/http";
import * as org from "@/lib/api/org";
import * as talk from "@/lib/api/collaboration";
import * as tasks from "@/lib/api/tasks";

const TENANT = "0193a7c4-0000-7000-8000-00000000d3e0";
const STANDUP = "01a1ed2a-e4bd-7110-8031-aa3e1601f3c9";
const caller: http.Caller = { tenantId: TENANT, actorId: "maria" };
const key = { value: "k-1" };
const person = { tenantId: TENANT, actor: { kind: "ACTOR_KIND_HUMAN", id: "maria" } };

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

const chart = {
    employees: [
        {
            employeeId: "co-ceo",
            name: "Cora",
            title: "Co-CEO",
            isCoCeo: true,
            managerId: "",
            departmentId: "",
            status: "EMPLOYEE_STATUS_ACTIVE",
            activeVersion: 1,
        },
        {
            employeeId: "sales-head",
            name: "Sam",
            title: "Head of Sales",
            isCoCeo: false,
            managerId: "co-ceo",
            departmentId: "sales",
            status: "EMPLOYEE_STATUS_ACTIVE",
            activeVersion: 1,
        },
    ],
    departments: [{ departmentId: "sales", name: "Sales", headEmployeeId: "sales-head" }],
};

describe("organisation", () => {
    it("reads the chart with pending hire requests", async () => {
        const wf = fake({
            GetOrgChart: chart,
            ListHireRequests: {
                requests: [
                    {
                        requestId: "r-1",
                        reason: "Busy",
                        requestedBy: "co-ceo",
                        status: "HIRE_REQUEST_STATUS_PENDING",
                        hire: { employeeId: "intern", name: "Ivy", title: "Intern" },
                    },
                ],
            },
        });
        const out = await org.getOrg(backends({ workforce: wf }), caller);
        expect(out.people.map((p) => [p.id, p.manager_id, p.is_co_ceo])).toEqual([
            ["co-ceo", null, true],
            ["sales-head", "co-ceo", false],
        ]);
        expect(out.departments[0]).toMatchObject({ id: "sales", head: "sales-head" });
        expect(out.hire_requests[0]).toMatchObject({
            id: "r-1",
            status: "pending",
            requested_by: "co-ceo",
        });
        expect(wf.calls[1]!.request.status).toBe("HIRE_REQUEST_STATUS_PENDING");
    });

    it("hires as the person, in one call, with the key", async () => {
        const wf = fake({
            HireEmployee: { employee: chart.employees[1], version: { version: 1 } },
        });
        await org.hire(backends({ workforce: wf }), caller, key, {
            id: "sales-head",
            name: "Sam",
            title: "Head of Sales",
            department_id: "sales",
            manager_id: "co-ceo",
            department_head: true,
            role: "Sells",
            model: "local-mock",
        });
        expect(wf.calls[0]).toEqual({
            method: "HireEmployee",
            request: {
                tenant: person,
                idempotencyKey: key,
                hire: {
                    employeeId: "sales-head",
                    name: "Sam",
                    title: "Head of Sales",
                    departmentId: "sales",
                    managerId: "co-ceo",
                    isCoCeo: false,
                    departmentHead: true,
                    role: "Sells",
                    instructions: "",
                    model: "local-mock",
                    maxCostUsd: "",
                },
            },
        });
    });

    it("refuses a bad hire before calling anything", async () => {
        const wf = fake();
        await expect(
            org.hire(backends({ workforce: wf }), caller, key, { id: "Bad Id" }),
        ).rejects.toMatchObject({ status: 400 });
        expect(wf.calls).toEqual([]);
    });

    it("decides a hire request", async () => {
        const wf = fake({
            DecideHireRequest: {
                request: { requestId: "r-1", status: "HIRE_REQUEST_STATUS_APPROVED" },
            },
        });
        const out = await org.decideHire(backends({ workforce: wf }), caller, "r-1", {
            approved: true,
            note: "ok",
        });
        expect(out.request.status).toBe("approved");
        expect(wf.calls[0]!.request).toEqual({
            tenant: person,
            requestId: "r-1",
            approved: true,
            note: "ok",
        });
    });

    it("gives a goal to the co-CEO as a task", async () => {
        const t = fake({ CreateTask: { task: { taskId: STANDUP, status: "TASK_STATUS_QUEUED" } } });
        const out = await org.giveGoal(
            backends({ workforce: fake({ GetOrgChart: chart }), tasks: t }),
            caller,
            key,
            { goal: "Win Acme this quarter" },
        );
        expect(out.task.id).toBe(STANDUP);
        expect(t.calls[0]!.request).toMatchObject({
            employeeId: "co-ceo",
            input: "Win Acme this quarter",
            idempotencyKey: key,
        });
    });

    it("says so when there is no co-CEO to take a goal", async () => {
        const wf = fake({ GetOrgChart: { employees: [chart.employees[1]], departments: [] } });
        await expect(
            org.giveGoal(backends({ workforce: wf }), caller, key, { goal: "x" }),
        ).rejects.toMatchObject({ status: 409, code: "no_co_ceo" });
    });
});

describe("conversations and standups", () => {
    it("lists and reads conversations", async () => {
        const c = fake({
            ListConversations: {
                conversations: [
                    {
                        conversationId: "c-1",
                        kind: "CONVERSATION_KIND_CALL",
                        topic: "Price",
                        status: "open",
                        participants: [],
                        lastMessage: { text: "hi", author: "a" },
                    },
                ],
            },
            GetConversation: {
                conversation: {
                    conversationId: "c-1",
                    kind: "CONVERSATION_KIND_CALL",
                    participants: [{ id: "a", kind: "employee" }],
                },
                messages: [
                    {
                        seq: 1,
                        author: "a",
                        authorKind: "employee",
                        kind: "question",
                        text: "Where?",
                    },
                ],
            },
        });
        const list = await talk.listConversations(
            backends({ collaboration: c }),
            caller,
            new URLSearchParams("kind=call&participant=a"),
        );
        expect(list.conversations[0]).toMatchObject({
            id: "c-1",
            kind: "call",
            last_message: "hi",
        });
        expect(c.calls[0]!.request).toMatchObject({
            kind: "CONVERSATION_KIND_CALL",
            participant: "a",
        });
        const one = await talk.getConversation(
            backends({ collaboration: c }),
            caller,
            "c-1",
            new URLSearchParams("after=0"),
        );
        expect(one.messages[0]).toMatchObject({ seq: 1, kind: "question", text: "Where?" });
    });

    it("refuses an unknown conversation kind", async () => {
        await expect(
            talk.listConversations(backends(), caller, new URLSearchParams("kind=party")),
        ).rejects.toMatchObject({ status: 400 });
    });

    it("posts as the person and maps a closed conversation to 409", async () => {
        const c = fake({ PostMessage: grpcError(Code.FAILED_PRECONDITION, "this call has ended") });
        await expect(
            talk.postMessage(backends({ collaboration: c }), caller, key, "c-1", { text: "late" }),
        ).rejects.toBeInstanceOf(Error);
        expect(c.calls[0]!.request).toEqual({
            tenant: person,
            idempotencyKey: key,
            conversationId: "c-1",
            text: "late",
        });
        expect(http.fromGrpc(grpcError(Code.FAILED_PRECONDITION, "x")).status).toBe(409);
    });

    it("starts a standup with a bounded discussion", async () => {
        const c = fake({ StartStandup: { standup: { standupId: STANDUP, status: "collecting" } } });
        const out = await talk.startStandup(backends({ collaboration: c }), caller, key, {
            department_id: "sales",
            discussion_s: 60,
        });
        expect(out.id).toBe(STANDUP);
        expect(c.calls[0]!.request).toEqual({
            tenant: person,
            idempotencyKey: key,
            departmentId: "sales",
            discussionS: 60,
            assignNextSteps: false,
        });
        await expect(
            talk.startStandup(backends(), caller, key, { discussion_s: 9999 }),
        ).rejects.toMatchObject({ status: 400 });
    });
});

describe("sub-tasks", () => {
    it("shows where a task sits in its tree and lists its sub-tasks", async () => {
        const t = fake({ ListTasks: { tasks: [] } });
        const mapped = tasks.task({
            taskId: "c",
            parentTaskId: "p",
            rootTaskId: "g",
            depth: 1,
            output: "done",
            delegatedBy: "sales-head",
            status: "TASK_STATUS_COMPLETED",
        });
        expect(mapped).toMatchObject({
            parent_task_id: "p",
            root_task_id: "g",
            depth: 1,
            output: "done",
            delegated_by: "sales-head",
        });
        await tasks.listTasks(
            backends({ tasks: t }),
            caller,
            new URLSearchParams(`parent=${STANDUP}`),
        );
        expect(t.calls[0]!.request).toMatchObject({ parentTaskId: STANDUP });
    });
});

describe("hire approvals show what is approved", () => {
    it("carries the model, the instructions, the spend limit and the place", () => {
        const r = org.hireRequest({
            requestId: "r-1",
            status: "HIRE_REQUEST_STATUS_PENDING",
            hire: {
                employeeId: "intern",
                name: "Ivy",
                model: "claude-sonnet",
                instructions: "Call every lead twice.",
                maxCostUsd: "2.00",
                isCoCeo: false,
                departmentHead: true,
            },
        });
        expect(r).toMatchObject({
            model: "claude-sonnet",
            instructions: "Call every lead twice.",
            max_cost_usd: "2.00",
            is_co_ceo: false,
            department_head: true,
        });
    });
});
