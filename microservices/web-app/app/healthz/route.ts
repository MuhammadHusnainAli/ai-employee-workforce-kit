// Liveness: the process answers. Never checks dependencies.
export const dynamic = "force-dynamic";

export function GET(): Response {
    return Response.json({ status: "ok" });
}
