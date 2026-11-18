// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { caller, handle, json } from "@/lib/api/http";
import * as br from "@/lib/api/brain";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const { id } = await ctx.params;
        return json(await br.getMemory(backends(), caller(req), id));
    });
}

export async function DELETE(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const { id } = await ctx.params;
        const q = new URL(req.url).searchParams;
        return json(await br.deleteMemory(backends(), caller(req), id, q));
    });
}
