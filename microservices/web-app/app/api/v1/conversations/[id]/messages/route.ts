// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { body, caller, handle, idempotencyKey, json } from "@/lib/api/http";
import * as talk from "@/lib/api/collaboration";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

export async function POST(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        const key = idempotencyKey(req);
        const { id } = await ctx.params;
        return json(await talk.postMessage(backends(), c, key, id, await body(req)), 201);
    });
}
