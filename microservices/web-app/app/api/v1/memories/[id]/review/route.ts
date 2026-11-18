// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { body, caller, handle, json } from "@/lib/api/http";
import * as br from "@/lib/api/brain";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

export async function POST(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const { id } = await ctx.params;
        const c = caller(req);
        return json(await br.reviewMemory(backends(), c, id, await body(req)));
    });
}
