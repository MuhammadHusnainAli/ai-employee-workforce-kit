// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { body, caller, handle, json } from "@/lib/api/http";
import * as org from "@/lib/api/org";

export const dynamic = "force-dynamic";

export async function POST(req: Request): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        return json(await org.createDepartment(backends(), c, await body(req)), 201);
    });
}
