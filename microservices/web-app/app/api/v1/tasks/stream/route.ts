// Server-sent task updates for the caller's tenant (see lib/api/events.ts).
import { caller, handle } from "@/lib/api/http";
import { taskStream } from "@/lib/api/events";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
    return handle(async () => {
        const c = caller(req);
        return new Response(taskStream(c.tenantId, req.signal), {
            headers: {
                "content-type": "text/event-stream",
                "cache-control": "no-cache, no-transform",
                connection: "keep-alive",
                "x-accel-buffering": "no",
            },
        });
    });
}
