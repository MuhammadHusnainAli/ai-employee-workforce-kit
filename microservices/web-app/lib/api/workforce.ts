// Employees and their versions (workforce-service). A version is immutable: changing an
// employee publishes a new one, and runs keep the version they started with.

import type { Backends } from "@/lib/api-client/grpc";
import { bad, type Caller, iso, str, tenant } from "@/lib/api/http";

type Msg = Record<string, unknown>;

export const TIERS = ["R0", "R1", "R2", "R3"] as const;
export type Tier = (typeof TIERS)[number];

/** The models the Model Gateway knows (deployments/local/litellm/config.yaml). */
export const MODELS = ["local-mock", "claude-haiku", "claude-sonnet"] as const;

/** The tools agent-runtime can run today (its tool catalogue). Others arrive with integrations. */
export const TOOLS: { name: string; tier: Tier; what: string }[] = [
    { name: "word_count", tier: "R0", what: "Counts the words in a text" },
    { name: "char_count", tier: "R0", what: "Counts the characters in a text" },
    { name: "current_time", tier: "R0", what: "Tells the current date and time" },
];

const EMPLOYEE_ID = /^[a-z][a-z0-9-]{1,62}$/;

export function employee(e: Msg) {
    return {
        id: e.employeeId,
        name: e.name,
        status: String(e.status ?? "")
            .replace("EMPLOYEE_STATUS_", "")
            .toLowerCase(),
        active_version: e.activeVersion ?? 0,
        created_at: iso(e.createdAt),
    };
}

export function version(v: Msg) {
    const limits = (v.limits as Msg) ?? {};
    return {
        version: v.version,
        role: v.role,
        instructions: v.instructions,
        model: v.model,
        tools: ((v.toolGrants as Msg[]) ?? []).map((g) => ({
            tool: g.tool,
            tier: String(g.tier ?? "").replace("RISK_TIER_", ""),
        })),
        auto_approve: (v.autoApprove as string[]) ?? [],
        max_steps: limits.maxSteps || null,
        max_cost_usd: limits.maxCostUsd || null,
        created_by: v.createdBy,
        created_at: iso(v.createdAt),
    };
}

export async function listEmployees(b: Backends, c: Caller) {
    const r = await b.workforce.call<Msg>("ListEmployees", { tenant: tenant(c) });
    return { employees: ((r.employees as Msg[]) ?? []).map(employee) };
}

/** The employee and its active version (null before the first publish). */
export async function getEmployee(b: Backends, c: Caller, id: string) {
    const e = await b.workforce.call<Msg>("GetEmployee", { tenant: tenant(c), employeeId: id });
    const emp = employee(e.employee as Msg);
    let active = null;
    if (Number(emp.active_version) > 0) {
        const v = await b.workforce.call<Msg>("GetVersion", {
            tenant: tenant(c),
            employeeId: id,
            version: 0,
        });
        active = version(v.version as Msg);
    }
    return { employee: emp, active_version: active };
}

function versionRequest(c: Caller, id: string, key: { value: string }, body: Msg) {
    const tools = Array.isArray(body.tools) ? (body.tools as Msg[]) : [];
    const grants = tools.map((t) => {
        const tier = str(t.tier, "tools[].tier", { required: true, max: 2 });
        if (!TIERS.includes(tier as Tier)) throw bad("tools[].tier must be R0, R1, R2 or R3");
        return {
            tool: str(t.tool, "tools[].tool", { required: true, max: 128 }),
            tier: `RISK_TIER_${tier}`,
        };
    });
    const maxSteps = body.max_steps ?? 0;
    if (
        typeof maxSteps !== "number" ||
        !Number.isInteger(maxSteps) ||
        maxSteps < 0 ||
        maxSteps > 100
    ) {
        throw bad("max_steps must be a whole number from 1 to 100");
    }
    const maxCost = body.max_cost_usd ?? "";
    if (maxCost !== "" && !/^\d+(\.\d{1,4})?$/.test(String(maxCost))) {
        throw bad("max_cost_usd must be a dollar amount like 0.50");
    }
    return {
        tenant: tenant(c),
        employeeId: id,
        idempotencyKey: key,
        role: str(body.role, "role", { required: true, max: 2000 }),
        instructions: str(body.instructions, "instructions", { max: 50_000 }),
        model: str(body.model, "model", { required: true, max: 200 }),
        toolGrants: grants,
        autoApprove: Array.isArray(body.auto_approve) ? (body.auto_approve as string[]) : [],
        limits: { maxSteps, maxCostUsd: String(maxCost) },
    };
}

/** Create the employee and publish its first version, so it can work at once. Idempotent:
 *  the same key creates nothing new. */
export async function createEmployee(b: Backends, c: Caller, key: { value: string }, body: Msg) {
    const id = str(body.id, "id", { required: true, max: 63 });
    if (!EMPLOYEE_ID.test(id)) {
        throw bad("id must start with a letter and use only lower-case letters, digits and '-'");
    }
    const name = str(body.name, "name", { required: true, max: 200 });
    const req = versionRequest(c, id, key, body); // validate everything before creating anything
    await b.workforce.call("CreateEmployee", { tenant: tenant(c), employeeId: id, name });
    await b.workforce.call("PublishVersion", req);
    return getEmployee(b, c, id);
}

export async function publishVersion(
    b: Backends,
    c: Caller,
    id: string,
    key: { value: string },
    body: Msg,
) {
    const r = await b.workforce.call<Msg>("PublishVersion", versionRequest(c, id, key, body));
    return version(r.version as Msg);
}

export async function setPaused(b: Backends, c: Caller, id: string, paused: boolean) {
    const r = await b.workforce.call<Msg>(paused ? "PauseEmployee" : "ResumeEmployee", {
        tenant: tenant(c),
        employeeId: id,
    });
    return employee(r.employee as Msg);
}
