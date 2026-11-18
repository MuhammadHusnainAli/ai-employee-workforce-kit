"use client";
// Tasks: give an employee work, and watch the list change as runs move.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, shortId, type Task, type TaskStatus, when } from "./api";
import { ErrorLine, Status } from "./bits";
import { useTaskEvents } from "./live";

const FILTERS: { value: TaskStatus | ""; label: string }[] = [
    { value: "", label: "All" },
    { value: "waiting_human", label: "Waiting for you" },
    { value: "running", label: "Working" },
    { value: "completed", label: "Done" },
    { value: "failed", label: "Failed" },
];

function NewTask({ onCreated }: { onCreated: (t: Task) => void }) {
    const [employee, setEmployee] = useState("summariser");
    const [input, setInput] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // One key per drafted task: a double click or a retry after a timeout creates it once.
    const [key, setKey] = useState(() => crypto.randomUUID());

    async function submit(e: React.FormEvent) {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            const t = await api<Task>("/tasks", {
                method: "POST",
                key,
                body: { employee_id: employee.trim(), input: input.trim() },
            });
            setInput("");
            setKey(crypto.randomUUID());
            onCreated(t);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <form className="compose" onSubmit={submit}>
            <label>
                <span>Employee</span>
                <input value={employee} onChange={(e) => setEmployee(e.target.value)} required />
            </label>
            <label>
                <span>What should they do?</span>
                <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Summarise this week's support tickets and flag anything urgent."
                    required
                    rows={2}
                />
            </label>
            <button type="submit" disabled={busy || !input.trim()}>
                {busy ? "Assigning" : "Assign task"}
            </button>
            <div style={{ gridColumn: "1 / -1" }}>
                <ErrorLine error={error} />
            </div>
        </form>
    );
}

export function TasksView({ initial }: { initial: Task[] | null }) {
    const [tasks, setTasks] = useState<Task[] | null>(initial);
    const [filter, setFilter] = useState<TaskStatus | "">("");
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(() => {
        const q = filter ? `?status=${filter}&limit=100` : "?limit=100";
        api<{ tasks: Task[] }>(`/tasks${q}`)
            .then((r) => {
                setTasks(r.tasks);
                setError(null);
            })
            .catch((e: Error) => setError(e.message));
    }, [filter]);

    useEffect(load, [load]);
    useTaskEvents(() => load());

    return (
        <>
            <header className="page-head">
                <div>
                    <h1>Tasks</h1>
                    <p className="lede">
                        Work you have given your employees. It updates as they go.
                    </p>
                </div>
            </header>
            <NewTask onCreated={(t) => setTasks((ts) => [t, ...(ts ?? [])])} />
            <div className="filters" role="group" aria-label="Show">
                {FILTERS.map((f) => (
                    <button
                        key={f.value || "all"}
                        type="button"
                        aria-pressed={filter === f.value}
                        onClick={() => setFilter(f.value)}
                    >
                        {f.label}
                    </button>
                ))}
            </div>
            <ErrorLine error={error} />
            {tasks && tasks.length === 0 ? (
                <div className="rows empty">
                    {filter
                        ? "Nothing here right now."
                        : "No tasks yet. Assign the first one above."}
                </div>
            ) : (
                <ul className="rows" aria-busy={tasks === null}>
                    {(tasks ?? []).map((t) => (
                        <li key={t.id}>
                            <Link href={`/tasks/${t.id}`} className="task-row">
                                <span className="task-title">{t.title || t.input}</span>
                                <Status value={t.status} />
                                <span className="meta">
                                    {t.employee_id}, assigned by {t.created_by} {when(t.created_at)}
                                </span>
                                <span className="meta">#{shortId(t.id)}</span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </>
    );
}
