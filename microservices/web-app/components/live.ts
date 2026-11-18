"use client";
// Live task events (/api/v1/tasks/stream) as a React hook. One EventSource per browser tab,
// shared by every component that listens; it reconnects on its own when the stream drops and
// closes when nobody listens any more.

import { useEffect, useRef } from "react";

export interface TaskEvent {
    type: string;
    task_id: string | null;
    status: string | null;
    stop_reason: string | null;
    at: string | null;
}

type Listener = (e: TaskEvent) => void;

const listeners = new Set<Listener>();
let source: EventSource | null = null;

function subscribe(fn: Listener): () => void {
    listeners.add(fn);
    if (!source) {
        source = new EventSource("/api/v1/tasks/stream");
        source.addEventListener("task", (m) => {
            let e: TaskEvent;
            try {
                e = JSON.parse((m as MessageEvent<string>).data) as TaskEvent;
            } catch {
                return; // a malformed event is skipped
            }
            for (const l of listeners) l(e);
        });
    }
    return () => {
        listeners.delete(fn);
        if (listeners.size === 0 && source) {
            source.close();
            source = null;
        }
    };
}

/** Call `onEvent` for every task event of this tenant while the component is mounted. */
export function useTaskEvents(onEvent: Listener): void {
    const handler = useRef(onEvent);
    handler.current = onEvent;
    useEffect(() => subscribe((e) => handler.current(e)), []);
}
