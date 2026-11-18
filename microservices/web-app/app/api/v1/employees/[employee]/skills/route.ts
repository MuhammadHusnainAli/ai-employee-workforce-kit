// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { caller, employee, handle, json } from "@/lib/api/http";
import * as br from "@/lib/api/brain";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ employee: string }> };

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const emp = employee((await ctx.params).employee);
        const q = new URL(req.url).searchParams;
        return json(await br.listSkills(backends(), caller(req), emp, q));
    });
}
