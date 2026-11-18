// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { body, caller, handle, json } from "@/lib/api/http";
import * as t from "@/lib/api/tasks";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

export async function POST(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const { id } = await ctx.params;
        const c = caller(req);
        return json(await t.cancelTask(backends(), c, id, await body(req)));
    });
}
