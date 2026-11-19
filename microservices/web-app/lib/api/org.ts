// The organisation: departments, who reports to whom, hiring, and goals for the co-CEO.
// The person using the web app is the CEO; one employee is the co-CEO and runs the company
// day to day. Hiring costs money, so only the person hires or approves a hire request.

import type { Backends } from "@/lib/api-client/grpc";
import { ApiError, bad, type Caller, iso, str, tenant } from "@/lib/api/http";
import { task } from "@/lib/api/tasks";

type Msg = Record<string, unknown>;

const ID = /^[a-z][a-z0-9-]{1,62}$/;
const lower = (v: unknown, prefix: string) =>
    String(v ?? "")
        .replace(prefix, "")
        .toLowerCase();

function id(v: unknown, name: string, required = true): string {
    const s = str(v, name, { required, max: 63 });
    if (s && !ID.test(s)) {
        throw bad(
            `${name} must start with a letter and use only lower-case letters, digits and "-"`,
        );
    }
    return s;
}

export function person(e: Msg) {
    return {
        id: e.employeeId as string,
        name: e.name as string,
        title: (e.title as string) ?? "",
        department_id: (e.departmentId as string) || null,
        manager_id: (e.managerId as string) || null,
        is_co_ceo: Boolean(e.isCoCeo),
        status: lower(e.status, "EMPLOYEE_STATUS_"),
        active_version: e.activeVersion ?? 0,
    };
}

export function department(d: Msg) {
    return {
        id: d.departmentId as string,
        name: d.name as string,
        head: (d.headEmployeeId as string) || null,
        created_at: iso(d.createdAt),
    };
}

export function hireRequest(r: Msg) {
    const h = (r.hire as Msg) ?? {};
    return {
        id: r.requestId as string,
        employee_id: h.employeeId as string,
        name: h.name as string,
        title: (h.title as string) ?? "",
        department_id: (h.departmentId as string) || null,
        manager_id: (h.managerId as string) || null,
        role: (h.role as string) ?? "",
        reason: (r.reason as string) ?? "",
        requested_by: r.requestedBy as string,
        status: lower(r.status, "HIRE_REQUEST_STATUS_"),
        decided_by: (r.decidedBy as string) || null,
        decision_note: (r.decisionNote as string) ?? "",
        created_at: iso(r.createdAt),
    };
}

export async function getOrg(b: Backends, c: Caller) {
    const chart = await b.workforce.call<Msg>("GetOrgChart", { tenant: tenant(c) });
    const pending = await b.workforce.call<Msg>("ListHireRequests", {
        tenant: tenant(c),
        status: "HIRE_REQUEST_STATUS_PENDING",
    });
    return {
        people: ((chart.employees as Msg[]) ?? []).map(person),
        departments: ((chart.departments as Msg[]) ?? []).map(department),
        hire_requests: ((pending.requests as Msg[]) ?? []).map(hireRequest),
    };
}

export async function createDepartment(b: Backends, c: Caller, body: Msg) {
    const r = await b.workforce.call<Msg>("CreateDepartment", {
        tenant: tenant(c),
        departmentId: id(body.id, "id"),
        name: str(body.name, "name", { required: true, max: 200 }),
    });
    return department(r.department as Msg);
}

/** Hire: create the employee, publish its first version and place it, in one call. */
export async function hire(b: Backends, c: Caller, key: { value: string }, body: Msg) {
    const maxCost = String(body.max_cost_usd ?? "");
    if (maxCost && !/^\d+(\.\d{1,4})?$/.test(maxCost)) {
        throw bad("max_cost_usd must be a dollar amount like 0.50");
    }
    const h = {
        employeeId: id(body.id, "id"),
        name: str(body.name, "name", { required: true, max: 200 }),
        title: str(body.title, "title", { max: 200 }),
        departmentId: id(body.department_id, "department_id", false),
        managerId: id(body.manager_id, "manager_id", false),
        isCoCeo: Boolean(body.is_co_ceo),
        departmentHead: Boolean(body.department_head),
        role: str(body.role, "role", { required: true, max: 2000 }),
        instructions: str(body.instructions, "instructions", { max: 50_000 }),
        model: str(body.model, "model", { required: true, max: 200 }),
        maxCostUsd: maxCost,
    };
    const r = await b.workforce.call<Msg>("HireEmployee", {
        tenant: tenant(c),
        idempotencyKey: key,
        hire: h,
    });
    return person(r.employee as Msg);
}

/** Move someone in the chart: title, department, manager. */
export async function place(b: Backends, c: Caller, employee: string, body: Msg) {
    const r = await b.workforce.call<Msg>("PlaceEmployee", {
        tenant: tenant(c),
        employeeId: id(employee, "employee"),
        title: str(body.title, "title", { max: 200 }),
        departmentId: id(body.department_id, "department_id", false),
        managerId: id(body.manager_id, "manager_id", false),
        isCoCeo: Boolean(body.is_co_ceo),
        departmentHead: Boolean(body.department_head),
    });
    return person(r.employee as Msg);
}

export async function decideHire(b: Backends, c: Caller, requestId: string, body: Msg) {
    if (typeof body.approved !== "boolean") throw bad("approved must be true or false");
    const r = await b.workforce.call<Msg>("DecideHireRequest", {
        tenant: tenant(c),
        requestId: str(requestId, "id", { required: true, max: 100 }),
        approved: body.approved,
        note: str(body.note, "note", { max: 2000 }),
    });
    return {
        request: hireRequest(r.request as Msg),
        employee: r.employee ? person(r.employee as Msg) : null,
    };
}

/** A goal for the company: a task for the co-CEO, who hands the pieces down. */
export async function giveGoal(b: Backends, c: Caller, key: { value: string }, body: Msg) {
    const goal = str(body.goal, "goal", { required: true, max: 100_000 }).trim();
    if (!goal) throw bad("goal is required");
    const chart = await b.workforce.call<Msg>("GetOrgChart", { tenant: tenant(c) });
    const ceo = ((chart.employees as Msg[]) ?? []).find((e) => e.isCoCeo);
    if (!ceo) {
        throw new ApiError(409, "no_co_ceo", "there is no co-CEO yet: hire one first");
    }
    const r = await b.tasks.call<Msg>("CreateTask", {
        tenant: tenant(c),
        idempotencyKey: key,
        employeeId: ceo.employeeId,
        title: str(body.title, "title", { max: 200 }) || goal.slice(0, 80),
        input: goal,
        priority: 5,
    });
    return { task: task(r.task as Msg) };
}
