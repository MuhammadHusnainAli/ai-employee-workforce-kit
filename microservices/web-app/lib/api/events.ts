// Live task updates as server-sent events: task-service's events (TASKS stream) for the caller's
// tenant only. Each stream reads from "now" through its own ordered consumer; nothing is stored.

import { connect, type NatsConnection } from "@nats-io/transport-node";
import { DeliverPolicy, jetstream } from "@nats-io/jetstream";

export const STREAM = "TASKS";
export const HEARTBEAT_MS = 15_000;

/** What a browser gets for one event, or null when it is not for this tenant. */
export function toSse(raw: string, tenantId: string): string | null {
    let e: Record<string, unknown>;
    try {
        e = JSON.parse(raw) as Record<string, unknown>;
    } catch {
        return null;
    }
    if (e.tenantid !== tenantId || typeof e.type !== "string" || !e.type.startsWith("task.")) {
        return null;
    }
    const data = (e.data as Record<string, unknown>) ?? {};
    const payload = {
        type: e.type,
        task_id: data.task_id ?? e.subject ?? null,
        status: data.to ?? null,
        stop_reason: data.stop_reason ?? null,
        at: e.time ?? null,
    };
    return `event: task\nid: ${String(e.id ?? "")}\ndata: ${JSON.stringify(payload)}\n\n`;
}

let shared: Promise<NatsConnection> | undefined;

function nats(): Promise<NatsConnection> {
    shared ??= connect({
        servers: process.env.WFK_NATS_URL ?? "nats://127.0.0.1:7020",
        maxReconnectAttempts: -1,
        name: "web-app",
    }).catch((err: unknown) => {
        shared = undefined; // try again on the next request
        throw err;
    });
    return shared;
}

/** A text/event-stream of the tenant's task events until the client goes away. */
export function taskStream(tenantId: string, signal: AbortSignal): ReadableStream<Uint8Array> {
    const enc = new TextEncoder();
    let stop = () => {};
    return new ReadableStream<Uint8Array>({
        async start(controller) {
            const send = (s: string) => {
                try {
                    controller.enqueue(enc.encode(s));
                } catch {
                    /* closed */
                }
            };
            send(": connected\n\n");
            const beat = setInterval(() => send(": heartbeat\n\n"), HEARTBEAT_MS);
            let closed = false;
            const close = () => {
                if (closed) return;
                closed = true;
                clearInterval(beat);
                stop();
                try {
                    controller.close();
                } catch {
                    /* already closed */
                }
            };
            signal.addEventListener("abort", close);
            try {
                const js = jetstream(await nats());
                const consumer = await js.consumers.get(STREAM, {
                    filter_subjects: ["task.>"],
                    deliver_policy: DeliverPolicy.New,
                });
                const messages = await consumer.consume();
                stop = () => void messages.stop();
                if (signal.aborted) return close();
                for await (const m of messages) {
                    const out = toSse(m.string(), tenantId);
                    if (out) send(out);
                }
            } catch (err) {
                send(
                    `event: error\ndata: ${JSON.stringify({ message: "updates unavailable" })}\n\n`,
                );
                console.error("task stream", err);
            }
            close();
        },
        cancel() {
            stop();
        },
    });
}
