// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { body, caller, handle, json } from "@/lib/api/http";
import * as org from "@/lib/api/org";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ employee: string }> };

export async function POST(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        const { employee } = await ctx.params;
        return json(await org.place(backends(), c, employee, await body(req)));
    });
}
