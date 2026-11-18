"use client";
// Everything waiting for a person, oldest first: the work that is stuck until someone answers.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, type Task, when } from "./api";
import { ErrorLine } from "./bits";
import { useTaskEvents } from "./live";

function Waiting({ task, onDone }: { task: Task; onDone: () => void }) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    async function decide(approved: boolean) {
        setBusy(true);
        setError(null);
        try {
            await api(`/tasks/${task.id}/approval`, { method: "POST", body: { approved } });
            onDone();
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <li>
            <div className="task-row">
                <Link href={`/tasks/${task.id}`} className="task-title">
                    {task.title || task.input}
                </Link>
                <div className="row-actions">
                    {task.stop_reason === "approval" ? (
                        <>
                            <button disabled={busy} onClick={() => decide(true)}>
                                Approve
                            </button>
                            <button className="quiet" disabled={busy} onClick={() => decide(false)}>
                                Decline
                            </button>
                        </>
                    ) : (
                        <Link href={`/tasks/${task.id}`}>Answer</Link>
                    )}
                </div>
                <span className="meta">
                    {task.employee_id} waiting {when(task.updated_at)}, reason:{" "}
                    {task.stop_reason || "a question"}
                </span>
            </div>
            <ErrorLine error={error} />
        </li>
    );
}

export function ApprovalsView({ initial }: { initial: Task[] | null }) {
    const [tasks, setTasks] = useState<Task[] | null>(initial);
    const [error, setError] = useState<string | null>(null);
    const load = useCallback(() => {
        api<{ tasks: Task[] }>("/tasks?status=waiting_human&limit=200")
            .then((r) => {
                setTasks([...r.tasks].reverse());
                setError(null);
            })
            .catch((e: Error) => setError(e.message));
    }, []);
    useEffect(load, [load]);
    useTaskEvents(() => load());

    return (
        <>
            <header className="page-head">
                <div>
                    <h1>Waiting for you</h1>
                    <p className="lede">
                        Employees stop here before anything risky, or when they need a person.
                        Oldest first.
                    </p>
                </div>
            </header>
            <ErrorLine error={error} />
            {tasks && tasks.length === 0 ? (
                <div className="rows empty">
                    Nobody is waiting on you. New requests appear here live.
                </div>
            ) : (
                <ul className="rows" aria-busy={tasks === null}>
                    {(tasks ?? []).map((t) => (
                        <Waiting key={t.id} task={t} onDone={load} />
                    ))}
                </ul>
            )}
        </>
    );
}
