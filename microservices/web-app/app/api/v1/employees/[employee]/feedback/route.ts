// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { body, caller, employee, handle, idempotencyKey, json } from "@/lib/api/http";
import * as br from "@/lib/api/brain";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ employee: string }> };

export async function POST(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const emp = employee((await ctx.params).employee);
        const c = caller(req);
        const key = idempotencyKey(req);
        return json(await br.giveFeedback(backends(), c, emp, key, await body(req)), 201);
    });
}
