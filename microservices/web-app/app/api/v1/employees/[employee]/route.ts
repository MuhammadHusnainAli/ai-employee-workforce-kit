// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { caller, employee, handle, json } from "@/lib/api/http";
import * as wf from "@/lib/api/workforce";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ employee: string }> };

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
    return handle(async () => {
        const emp = employee((await ctx.params).employee);
        return json(await wf.getEmployee(backends(), caller(req), emp));
    });
}
