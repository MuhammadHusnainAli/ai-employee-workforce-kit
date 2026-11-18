// HTTP plumbing for the public API: who is calling, errors as JSON, gRPC codes as HTTP statuses.

import { status as Code } from "@grpc/grpc-js";

/** The caller, as the backends see it (wfk.common.v1.TenantContext). */
export interface Caller {
    tenantId: string;
    actorId: string;
}

export class ApiError extends Error {
    constructor(
        readonly status: number,
        readonly code: string,
        message: string,
        readonly retryable = false,
    ) {
        super(message);
    }
}

export const bad = (msg: string) => new ApiError(400, "invalid_argument", msg);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (s: string) => UUID.test(s);

/**
 * Who is calling. Production puts Keycloak (OIDC) in front and maps the token's tenant and
 * subject here; that is not wired yet. Local development (WFK_AUTH_MODE=dev, the default) acts
 * as one tenant (WFK_DEV_TENANT) and takes the person from `X-WFK-Actor`. With
 * WFK_API_TOKEN set, requests must also carry `Authorization: Bearer <token>`.
 */
export function caller(req: Request): Caller {
    const env = process.env;
    const token = env.WFK_API_TOKEN;
    if (token && req.headers.get("authorization") !== `Bearer ${token}`) {
        throw new ApiError(401, "unauthenticated", "a valid bearer token is required");
    }
    if ((env.WFK_AUTH_MODE ?? "dev") !== "dev") {
        throw new ApiError(501, "unimplemented", "only WFK_AUTH_MODE=dev is available so far");
    }
    const actor = (req.headers.get("x-wfk-actor") ?? "").trim() || "dev-user";
    if (actor.length > 200) throw bad("X-WFK-Actor is at most 200 characters");
    return {
        tenantId: env.WFK_DEV_TENANT ?? "0193a7c4-0000-7000-8000-00000000d3e0",
        actorId: actor,
    };
}

/** The TenantContext for a backend call: a person acting through the web app. */
export const tenant = (c: Caller) => ({
    tenantId: c.tenantId,
    actor: { kind: "ACTOR_KIND_HUMAN", id: c.actorId },
});

/** Mutating calls need an Idempotency-Key header, so a retried request changes nothing twice. */
export function idempotencyKey(req: Request): { value: string } {
    const key = (req.headers.get("idempotency-key") ?? "").trim();
    if (!key || key.length > 200) {
        throw bad("the Idempotency-Key header is required (at most 200 characters)");
    }
    return { value: key };
}

export async function body(req: Request): Promise<Record<string, unknown>> {
    try {
        const v: unknown = await req.json();
        if (v && typeof v === "object" && !Array.isArray(v)) return v as Record<string, unknown>;
    } catch {
        /* fall through */
    }
    throw bad("the body must be a JSON object");
}

export const str = (v: unknown, name: string, { required = false, max = 100_000 } = {}) => {
    if (v === undefined || v === null || v === "") {
        if (required) throw bad(`${name} is required`);
        return "";
    }
    if (typeof v !== "string") throw bad(`${name} must be a string`);
    if (v.length > max) throw bad(`${name} is at most ${max} characters`);
    return v;
};

/** gRPC status -> HTTP status, and whether trying again later can help. */
const MAP: Record<number, [number, string, boolean]> = {
    [Code.INVALID_ARGUMENT]: [400, "invalid_argument", false],
    [Code.NOT_FOUND]: [404, "not_found", false],
    [Code.ALREADY_EXISTS]: [409, "already_exists", false],
    [Code.FAILED_PRECONDITION]: [409, "failed_precondition", false],
    [Code.ABORTED]: [409, "aborted", true],
    [Code.PERMISSION_DENIED]: [403, "permission_denied", false],
    [Code.UNAUTHENTICATED]: [401, "unauthenticated", false],
    [Code.RESOURCE_EXHAUSTED]: [429, "resource_exhausted", true],
    [Code.UNAVAILABLE]: [503, "unavailable", true],
    [Code.DEADLINE_EXCEEDED]: [504, "deadline_exceeded", true],
    [Code.UNIMPLEMENTED]: [501, "unimplemented", false],
};

export function fromGrpc(err: unknown): ApiError {
    if (err instanceof ApiError) return err;
    const e = err as { code?: number; details?: string; message?: string };
    if (typeof e.code === "number" && MAP[e.code]) {
        const [status, code, retryable] = MAP[e.code]!;
        return new ApiError(status, code, e.details || e.message || code, retryable);
    }
    return new ApiError(500, "internal", "internal error", true);
}

export function json(data: unknown, status = 200, headers: Record<string, string> = {}): Response {
    return new Response(JSON.stringify(data), {
        status,
        headers: { "content-type": "application/json", ...headers },
    });
}

/** Run a handler; any failure becomes `{"error": {code, message, retryable}}`. */
export async function handle(fn: () => Promise<Response>): Promise<Response> {
    try {
        return await fn();
    } catch (err) {
        const e = fromGrpc(err);
        if (e.status >= 500) console.error("api error", err);
        const headers: Record<string, string> = e.retryable ? { "retry-after": "2" } : {};
        return json(
            { error: { code: e.code, message: e.message, retryable: e.retryable } },
            e.status,
            headers,
        );
    }
}

/** A protobuf Timestamp as an ISO string (null when unset). */
export function iso(ts: unknown): string | null {
    const t = ts as { seconds?: string | number; nanos?: number } | null | undefined;
    if (!t || t.seconds === undefined) return null;
    const ms = Number(t.seconds) * 1000 + Math.floor((t.nanos ?? 0) / 1e6);
    return ms > 0 ? new Date(ms).toISOString() : null;
}

const EMPLOYEE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

/** An employee id from the path. */
export function employee(v: string): string {
    if (!EMPLOYEE.test(v))
        throw bad("employee id must be 1 to 100 letters, digits, '.', '_' or '-'");
    return v;
}
