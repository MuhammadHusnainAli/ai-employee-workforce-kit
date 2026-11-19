// Thin route: resolve the caller, read the input, call the handler, map errors.
import { realBackends as backends } from "@/lib/api-client/grpc";
import { caller, handle, json } from "@/lib/api/http";
import * as org from "@/lib/api/org";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        return json(await org.getOrg(backends(), c));
    });
}
