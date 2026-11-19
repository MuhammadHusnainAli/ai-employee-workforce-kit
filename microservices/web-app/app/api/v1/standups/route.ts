// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { body, caller, handle, idempotencyKey, json } from "@/lib/api/http";
import * as talk from "@/lib/api/collaboration";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        return json(await talk.listStandups(backends(), c));
    });
}

export async function POST(req: Request): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        const key = idempotencyKey(req);
        return json(await talk.startStandup(backends(), c, key, await body(req)), 201);
    });
}
