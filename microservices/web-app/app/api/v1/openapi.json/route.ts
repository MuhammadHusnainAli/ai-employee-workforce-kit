// The public API's contract (contracts/openapi.json), the source of truth for every route.
import spec from "@/contracts/openapi.json";

export const dynamic = "force-static";

export function GET(): Response {
    return Response.json(spec);
}
