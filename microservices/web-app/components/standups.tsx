"use client";
// Standups run by the co-CEO: everyone says what they finished, what is next and what blocks
// them (from their real tasks), you can join the discussion, and the co-CEO sums up where we
// are and where we need to go.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, when } from "./api";
import { ErrorLine } from "./bits";
import { Thread } from "./conversations";

interface Report {
    employee_id: string;
    title: string;
    done: string;
    next: string;
    blockers: string;
    idle: boolean;
}

export interface Standup {
    id: string;
    department_id: string | null;
    status: "collecting" | "discussing" | "summarising" | "done" | "failed";
    led_by: string | null;
    started_by: string;
    discussion_s: number;
    assign_next_steps: boolean;
    reports: Report[];
    where_we_are: string;
    where_we_go: string;
    next_steps: { owner: string; action: string; task_id: string | null }[];
    risks: string[];
    error: string | null;
    created_at: string | null;
    finished_at: string | null;
}

const STATUS_TEXT: Record<Standup["status"], string> = {
    collecting: "Collecting updates",
    discussing: "Open for you to join",
    summarising: "Summing up",
    done: "Done",
    failed: "Could not run",
};
const POLL_MS = 2_000;

function statusChip(s: Standup["status"]) {
    const cls =
        s === "discussing"
            ? "status status-waiting"
            : s === "failed"
              ? "status status-failed"
              : s === "done"
                ? "status status-completed"
                : "status status-running";
    return <span className={cls}>{STATUS_TEXT[s]}</span>;
}

function StartForm() {
    const router = useRouter();
    const [depts, setDepts] = useState<{ id: string; name: string }[]>([]);
    const [dept, setDept] = useState("");
    const [minutes, setMinutes] = useState(2);
    const [assign, setAssign] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [key] = useState(() => crypto.randomUUID());
    useEffect(() => {
        api<{ departments: { id: string; name: string }[] }>("/org")
            .then((o) => setDepts(o.departments))
            .catch(() => setDepts([]));
    }, []);
    async function submit(e: React.FormEvent) {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            const s = await api<Standup>("/standups", {
                method: "POST",
                key,
                body: {
                    department_id: dept,
                    discussion_s: Math.round(minutes * 60),
                    assign_next_steps: assign,
                },
            });
            router.push(`/standups/${s.id}`);
        } catch (err) {
            setError((err as Error).message);
            setBusy(false);
        }
    }
    return (
        <form className="compose" onSubmit={submit}>
            <label>
                <span>Who reports</span>
                <select value={dept} onChange={(e) => setDept(e.target.value)}>
                    <option value="">The whole company</option>
                    {depts.map((d) => (
                        <option key={d.id} value={d.id}>
                            {d.name}
                        </option>
                    ))}
                </select>
            </label>
            <div>
                <label>
                    <span>Minutes of quiet before the discussion closes (0: no discussion)</span>
                    <input
                        type="number"
                        min={0}
                        max={60}
                        step={0.5}
                        value={minutes}
                        onChange={(e) => setMinutes(Number(e.target.value))}
                    />
                </label>
                <label className="check">
                    <input
                        type="checkbox"
                        checked={assign}
                        onChange={(e) => setAssign(e.target.checked)}
                    />
                    Turn the next steps into tasks
                </label>
            </div>
            <button type="submit" disabled={busy}>
                Start a standup
            </button>
            <ErrorLine error={error} />
        </form>
    );
}

export function StandupsView({ initial }: { initial: Standup[] | null }) {
    const [list, setList] = useState<Standup[] | null>(initial);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        const load = () =>
            api<{ standups: Standup[] }>("/standups")
                .then((r) => {
                    setList(r.standups);
                    setError(null);
                })
                .catch((e: Error) => setError(e.message));
        load();
        const t = setInterval(load, POLL_MS * 3);
        return () => clearInterval(t);
    }, []);
    return (
        <>
            <header className="page-head">
                <div>
                    <h1>Standups</h1>
                    <p className="lede">
                        Your co-CEO asks everyone what they finished, what is next and what blocks
                        them, from their real tasks. Join while it is open, then read where you are
                        and where you need to go.
                    </p>
                </div>
            </header>
            <StartForm />
            <ErrorLine error={error} />
            {list && list.length === 0 ? (
                <div className="rows empty">No standups yet.</div>
            ) : (
                <ul className="rows" aria-busy={list === null}>
                    {(list ?? []).map((s) => (
                        <li key={s.id}>
                            <Link href={`/standups/${s.id}`} className="task-row">
                                <span className="task-title">
                                    {s.department_id
                                        ? `${s.department_id} standup`
                                        : "Company standup"}
                                </span>
                                {statusChip(s.status)}
                                <span className="meta">
                                    {s.where_we_go
                                        ? `Where we go: ${s.where_we_go}`
                                        : `${s.reports.length} updates so far`}
                                </span>
                                <span className="meta">{when(s.created_at)}</span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </>
    );
}

export function StandupRoom({ id }: { id: string }) {
    const [s, setS] = useState<Standup | null>(null);
    const [error, setError] = useState<string | null>(null);
    const load = useCallback(() => {
        api<Standup>(`/standups/${id}`)
            .then((x) => {
                setS(x);
                setError(null);
            })
            .catch((e: Error) => setError(e.message));
    }, [id]);
    useEffect(() => {
        load();
        const t = setInterval(load, POLL_MS);
        return () => clearInterval(t);
    }, [load]);

    async function end() {
        try {
            await api(`/standups/${id}/end`, { method: "POST", body: {} });
            load();
        } catch (err) {
            setError((err as Error).message);
        }
    }

    const finished = s?.status === "done" || s?.status === "failed";
    return (
        <>
            <header className="page-head">
                <div>
                    <p className="meta">
                        <Link href="/standups">Standups</Link>
                    </p>
                    <h1>{s?.department_id ? `${s.department_id} standup` : "Company standup"}</h1>
                    {s && (
                        <p className="lede">
                            Run by {s.led_by ?? "the co-CEO"}, started by {s.started_by}{" "}
                            {when(s.created_at)}. {statusChip(s.status)}
                        </p>
                    )}
                </div>
                {s && !finished && s.status !== "summarising" && (
                    <button type="button" className="quiet" onClick={end}>
                        End the discussion and sum up
                    </button>
                )}
            </header>
            <ErrorLine error={error} />
            {s?.error && <p className="error">{s.error}</p>}
            {s && (s.where_we_are || s.where_we_go) && (
                <section className="section summary" aria-labelledby="summary">
                    <h2 id="summary">Where we stand</h2>
                    <dl className="facts">
                        <dt>Where we are</dt>
                        <dd className="prose">{s.where_we_are}</dd>
                        <dt>Where we go</dt>
                        <dd className="prose">{s.where_we_go}</dd>
                        {s.risks.length > 0 && (
                            <>
                                <dt>Risks</dt>
                                <dd>{s.risks.join("; ")}</dd>
                            </>
                        )}
                    </dl>
                    {s.next_steps.length > 0 && (
                        <ol className="next-steps">
                            {s.next_steps.map((n, i) => (
                                <li key={i}>
                                    <strong>{n.owner === "person" ? "You" : n.owner}</strong>:{" "}
                                    {n.action}
                                    {n.task_id && (
                                        <>
                                            {" "}
                                            <Link href={`/tasks/${n.task_id}`}>(task)</Link>
                                        </>
                                    )}
                                </li>
                            ))}
                        </ol>
                    )}
                </section>
            )}
            <section className="section" aria-labelledby="updates">
                <h2 id="updates">Updates</h2>
                {s && s.reports.length === 0 && <p className="meta">Collecting updates.</p>}
                <ul className="reports">
                    {(s?.reports ?? []).map((r) => (
                        <li
                            key={r.employee_id}
                            className={r.idle ? "report report-idle" : "report"}
                        >
                            <p className="task-title">
                                {r.employee_id}
                                {r.title ? <span className="meta">, {r.title}</span> : null}
                            </p>
                            {r.idle ? (
                                <p className="meta">{r.done}</p>
                            ) : (
                                <dl className="facts">
                                    <dt>Done</dt>
                                    <dd>{r.done || "-"}</dd>
                                    <dt>Next</dt>
                                    <dd>{r.next || "-"}</dd>
                                    {r.blockers && (
                                        <>
                                            <dt>Blocked</dt>
                                            <dd className="blocked">{r.blockers}</dd>
                                        </>
                                    )}
                                </dl>
                            )}
                        </li>
                    ))}
                </ul>
            </section>
            <section className="section" aria-labelledby="talk">
                <h2 id="talk">Discussion</h2>
                <Thread id={id} kinds={["message", "system"]} />
            </section>
        </>
    );
}
