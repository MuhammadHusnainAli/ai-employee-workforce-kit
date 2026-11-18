"use client";
// Small shared pieces: status and trust marks, the person picker, error lines.

import { useEffect, useState } from "react";
import { currentActor, setActor, type TaskStatus } from "./api";

const STATUS_TEXT: Record<TaskStatus, string> = {
    queued: "Queued",
    running: "Working",
    waiting_human: "Waiting for you",
    completed: "Done",
    failed: "Failed",
    cancelled: "Cancelled",
};

export function Status({ value }: { value: TaskStatus }) {
    return <span className={`status status-${value}`}>{STATUS_TEXT[value] ?? value}</span>;
}

const TRUST_TEXT: Record<string, string> = {
    human_verified: "Confirmed by a person",
    system: "Recorded by the platform",
    observed: "Seen while working",
    external: "From outside",
};

export function Trust({ value }: { value: string }) {
    return (
        <span className={`trust trust-${value}`} title={TRUST_TEXT[value]}>
            {TRUST_TEXT[value] ?? value}
        </span>
    );
}

export function ErrorLine({ error }: { error: string | null }) {
    if (!error) return null;
    return (
        <p className="error" role="alert">
            {error}
        </p>
    );
}

/** Local development acts as one tenant; this names the person (sent as X-WFK-Actor). */
export function ActorPicker() {
    const [actor, setLocal] = useState("maria");
    useEffect(() => {
        setLocal(currentActor());
        const sync = () => setLocal(currentActor());
        window.addEventListener("wfk-actor", sync);
        return () => window.removeEventListener("wfk-actor", sync);
    }, []);
    return (
        <label className="actor">
            <span>Signed in as</span>
            <input
                value={actor}
                onChange={(e) => setLocal(e.target.value)}
                onBlur={() => setActor(actor.trim() || "maria")}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                aria-label="Your name"
                maxLength={60}
            />
        </label>
    );
}
