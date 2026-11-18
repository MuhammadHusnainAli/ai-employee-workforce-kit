// Readiness: the services the API needs accept connections in time.
import * as grpc from "@grpc/grpc-js";

export const dynamic = "force-dynamic";

const WAIT_MS = 1_500;

function targets(): Record<string, string> {
    const env = process.env;
    return {
        "task-service": env.WFK_TASK_SERVICE_ADDR ?? "127.0.0.1:7004",
        "agent-runtime": env.WFK_RUN_SERVICE_ADDR ?? "127.0.0.1:7002",
        "brain-service": env.WFK_BRAIN_SERVICE_ADDR ?? "127.0.0.1:7003",
        "workforce-service": env.WFK_WORKFORCE_SERVICE_ADDR ?? "127.0.0.1:7001",
    };
}

function reachable(address: string): Promise<"ok" | "unreachable"> {
    return new Promise((resolve) => {
        const ch = new grpc.Channel(address, grpc.credentials.createInsecure(), {});
        const deadline = Date.now() + WAIT_MS;
        const done = (v: "ok" | "unreachable") => {
            ch.close();
            resolve(v);
        };
        const wait = () => {
            const state = ch.getConnectivityState(true);
            if (state === grpc.connectivityState.READY) return done("ok");
            ch.watchConnectivityState(state, deadline, (err) =>
                err ? done("unreachable") : wait(),
            );
        };
        wait();
    });
}

export async function GET(): Promise<Response> {
    const entries = await Promise.all(
        Object.entries(targets()).map(
            async ([name, addr]) => [name, await reachable(addr)] as const,
        ),
    );
    const checks = Object.fromEntries(entries);
    const ready = Object.values(checks).every((v) => v === "ok");
    return Response.json(
        { status: ready ? "ok" : "unavailable", checks },
        { status: ready ? 200 : 503 },
    );
}
