// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { caller, handle, json } from "@/lib/api/http";
import * as talk from "@/lib/api/collaboration";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        const { id } = await ctx.params;
        return json(await talk.getConversation(backends(), c, id, new URL(req.url).searchParams));
    });
}
