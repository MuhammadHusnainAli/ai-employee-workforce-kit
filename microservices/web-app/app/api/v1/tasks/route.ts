// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { body, caller, handle, idempotencyKey, json } from "@/lib/api/http";
import * as t from "@/lib/api/tasks";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
    return handle(async () => {
        const q = new URL(req.url).searchParams;
        return json(await t.listTasks(backends(), caller(req), q));
    });
}

export async function POST(req: Request): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        const key = idempotencyKey(req);
        return json(await t.createTask(backends(), c, key, await body(req)), 201);
    });
}
