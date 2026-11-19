"use client";
// The organisation: you are the CEO; your co-CEO runs the company. Give it a goal, hire people
// into departments under a manager, and decide the hires your team asks for (a hire costs money,
// so only you hire).

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, when } from "./api";
import { ErrorLine } from "./bits";

export interface Person {
    id: string;
    name: string;
    title: string;
    department_id: string | null;
    manager_id: string | null;
    is_co_ceo: boolean;
    status: string;
}

export interface Department {
    id: string;
    name: string;
    head: string | null;
}

interface HireRequest {
    id: string;
    employee_id: string;
    name: string;
    title: string;
    department_id: string | null;
    manager_id: string | null;
    role: string;
    reason: string;
    requested_by: string;
    created_at: string | null;
}

export interface Org {
    people: Person[];
    departments: Department[];
    hire_requests: HireRequest[];
}

const MODELS = [
    { id: "local-mock", label: "Local test model (no key needed)" },
    { id: "claude-haiku", label: "Claude Haiku" },
    { id: "claude-sonnet", label: "Claude Sonnet" },
];

const slug = (s: string) =>
    s
        .toLowerCase()
        .normalize("NFKD")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .replace(/^([^a-z])/, "e-$1")
        .slice(0, 63);

function GoalBox({ ceo }: { ceo: Person }) {
    const [goal, setGoal] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [given, setGiven] = useState<string | null>(null);
    const [key, setKey] = useState(() => crypto.randomUUID());

    async function submit(e: React.FormEvent) {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            const r = await api<{ task: { id: string } }>("/org/goals", {
                method: "POST",
                key,
                body: { goal: goal.trim() },
            });
            setGiven(r.task.id);
            setGoal("");
            setKey(crypto.randomUUID());
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <form className="goal" onSubmit={submit}>
            <label>
                <span>
                    A goal for {ceo.name}, your co-CEO. It splits the work and hands it down the
                    chart.
                </span>
                <textarea
                    value={goal}
                    onChange={(e) => setGoal(e.target.value)}
                    rows={3}
                    placeholder="Book ten demos with mid-size logistics companies by the end of the month."
                    required
                />
            </label>
            <ErrorLine error={error} />
            <div className="row-actions">
                <button type="submit" disabled={busy || !goal.trim()}>
                    Give the goal
                </button>
                {given && (
                    <Link href={`/tasks/${given}`}>
                        Follow it: the goal and everything below it
                    </Link>
                )}
            </div>
        </form>
    );
}

function HireForm({ org, onDone }: { org: Org; onDone: () => void }) {
    const hasCeo = org.people.some((p) => p.is_co_ceo);
    const [f, setF] = useState({
        name: "",
        id: "",
        title: hasCeo ? "" : "Co-CEO",
        department_id: "",
        manager_id: org.people.find((p) => p.is_co_ceo)?.id ?? "",
        is_co_ceo: !hasCeo,
        department_head: false,
        role: hasCeo ? "" : "Runs the company day to day for the CEO.",
        instructions: "",
        model: "local-mock",
        max_cost_usd: "0.50",
    });
    const [idTouched, setIdTouched] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [key] = useState(() => crypto.randomUUID());
    const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) =>
        setF((x) => ({ ...x, [k]: v }));

    async function submit(e: React.FormEvent) {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            await api("/org/hires", {
                method: "POST",
                key,
                body: { ...f, manager_id: f.is_co_ceo ? "" : f.manager_id },
            });
            onDone();
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <form className="employee-form" onSubmit={submit}>
            <h2>Hire</h2>
            <div className="two">
                <label>
                    <span>Name</span>
                    <input
                        value={f.name}
                        onChange={(e) => {
                            set("name", e.target.value);
                            if (!idTouched) set("id", slug(e.target.value));
                        }}
                        placeholder="Sam Rivera"
                        required
                    />
                </label>
                <label>
                    <span>ID (used in tasks and the API)</span>
                    <input
                        value={f.id}
                        onChange={(e) => {
                            setIdTouched(true);
                            set("id", e.target.value);
                        }}
                        pattern="[a-z][a-z0-9-]{1,62}"
                        required
                    />
                </label>
            </div>
            <div className="two">
                <label>
                    <span>Title</span>
                    <input
                        value={f.title}
                        onChange={(e) => set("title", e.target.value)}
                        placeholder="Junior Sales"
                    />
                </label>
                <label>
                    <span>Department</span>
                    <select
                        value={f.department_id}
                        onChange={(e) => set("department_id", e.target.value)}
                    >
                        <option value="">None</option>
                        {org.departments.map((d) => (
                            <option key={d.id} value={d.id}>
                                {d.name}
                            </option>
                        ))}
                    </select>
                </label>
            </div>
            <div className="two">
                {!f.is_co_ceo && (
                    <label>
                        <span>Reports to</span>
                        <select
                            value={f.manager_id}
                            onChange={(e) => set("manager_id", e.target.value)}
                        >
                            <option value="">You (the CEO)</option>
                            {org.people.map((p) => (
                                <option key={p.id} value={p.id}>
                                    {p.name}
                                    {p.title ? `, ${p.title}` : ""}
                                </option>
                            ))}
                        </select>
                    </label>
                )}
                <div>
                    {!hasCeo && (
                        <label className="check">
                            <input
                                type="checkbox"
                                checked={f.is_co_ceo}
                                onChange={(e) => set("is_co_ceo", e.target.checked)}
                            />
                            This is the co-CEO
                        </label>
                    )}
                    {f.department_id && (
                        <label className="check">
                            <input
                                type="checkbox"
                                checked={f.department_head}
                                onChange={(e) => set("department_head", e.target.checked)}
                            />
                            Heads the department
                        </label>
                    )}
                </div>
            </div>
            <label>
                <span>Role: what is this person for?</span>
                <input
                    value={f.role}
                    onChange={(e) => set("role", e.target.value)}
                    placeholder="Finds and qualifies new leads for the sales team."
                    required
                />
            </label>
            <label>
                <span>Instructions: how should they work?</span>
                <textarea
                    value={f.instructions}
                    onChange={(e) => set("instructions", e.target.value)}
                    rows={4}
                />
            </label>
            <div className="two">
                <label>
                    <span>Model</span>
                    <select value={f.model} onChange={(e) => set("model", e.target.value)}>
                        {MODELS.map((m) => (
                            <option key={m.id} value={m.id}>
                                {m.label}
                            </option>
                        ))}
                    </select>
                </label>
                <label>
                    <span>Most spend per task ($)</span>
                    <input
                        value={f.max_cost_usd}
                        onChange={(e) => set("max_cost_usd", e.target.value)}
                        inputMode="decimal"
                    />
                </label>
            </div>
            <p className="meta">
                Everyone can message colleagues, call them with a question and hand work to the
                people below them. The co-CEO can also ask you for hires and run standups.
            </p>
            <ErrorLine error={error} />
            <div className="row-actions">
                <button type="submit" disabled={busy}>
                    Hire
                </button>
                <button type="button" className="quiet" onClick={onDone}>
                    Cancel
                </button>
            </div>
        </form>
    );
}

function DepartmentForm({ onDone }: { onDone: () => void }) {
    const [name, setName] = useState("");
    const [error, setError] = useState<string | null>(null);
    async function submit(e: React.FormEvent) {
        e.preventDefault();
        setError(null);
        try {
            await api("/org/departments", {
                method: "POST",
                body: { id: slug(name), name: name.trim() },
            });
            setName("");
            onDone();
        } catch (err) {
            setError((err as Error).message);
        }
    }
    return (
        <form className="inline-form" onSubmit={submit}>
            <label>
                <span>New department</span>
                <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Sales"
                    required
                />
            </label>
            <button type="submit">Add</button>
            <ErrorLine error={error} />
        </form>
    );
}

function HireRequests({ org, onDone }: { org: Org; onDone: () => void }) {
    const [error, setError] = useState<string | null>(null);
    const name = (id: string | null) =>
        id ? (org.people.find((p) => p.id === id)?.name ?? id) : "you";
    async function decide(id: string, approved: boolean) {
        setError(null);
        try {
            await api(`/org/hire-requests/${id}/decide`, { method: "POST", body: { approved } });
            onDone();
        } catch (err) {
            setError((err as Error).message);
        }
    }
    if (!org.hire_requests.length) return null;
    return (
        <section className="section waiting-section" aria-labelledby="hires">
            <h2 id="hires">Hires waiting for you</h2>
            <ErrorLine error={error} />
            <ul className="rows">
                {org.hire_requests.map((r) => (
                    <li key={r.id} className="hire-request">
                        <div>
                            <p className="task-title">
                                {r.name}
                                {r.title ? `, ${r.title}` : ""}
                            </p>
                            <p className="meta">
                                Asked by {name(r.requested_by)} {when(r.created_at)}. Reports to{" "}
                                {name(r.manager_id)}
                                {r.department_id ? `, in ${r.department_id}` : ""}.
                            </p>
                            <p>{r.role}</p>
                            {r.reason && <p className="prose">Why: {r.reason}</p>}
                        </div>
                        <div className="row-actions">
                            <button type="button" onClick={() => decide(r.id, true)}>
                                Approve the hire
                            </button>
                            <button
                                type="button"
                                className="quiet"
                                onClick={() => decide(r.id, false)}
                            >
                                Not now
                            </button>
                        </div>
                    </li>
                ))}
            </ul>
        </section>
    );
}

function Node({
    p,
    reports,
    depts,
}: {
    p: Person;
    reports: Map<string, Person[]>;
    depts: Map<string, string>;
}) {
    const below = reports.get(p.id) ?? [];
    return (
        <li>
            <div className={`node${p.is_co_ceo ? " node-ceo" : ""}`}>
                <span className="task-title">{p.name}</span>
                <span className="meta">
                    {p.title || (p.is_co_ceo ? "Co-CEO" : "No title")}
                    {p.department_id ? `, ${depts.get(p.department_id) ?? p.department_id}` : ""}
                </span>
                {p.status === "paused" && <span className="chip chip-held">Paused</span>}
                <span className="node-links">
                    <Link href={`/tasks?employee=${p.id}`}>Tasks</Link>
                    <Link href={`/conversations?participant=${p.id}`}>Conversations</Link>
                </span>
            </div>
            {below.length > 0 && (
                <ul>
                    {below.map((c) => (
                        <Node key={c.id} p={c} reports={reports} depts={depts} />
                    ))}
                </ul>
            )}
        </li>
    );
}

export function OrgView({ initial }: { initial: Org | null }) {
    const [org, setOrg] = useState<Org | null>(initial);
    const [hiring, setHiring] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(() => {
        api<Org>("/org")
            .then((o) => {
                setOrg(o);
                setError(null);
            })
            .catch((e: Error) => setError(e.message));
    }, []);
    useEffect(load, [load]);

    const { ceo, top, reports, depts } = useMemo(() => {
        const people = org?.people ?? [];
        const reports = new Map<string, Person[]>();
        for (const p of people) {
            if (p.manager_id) reports.set(p.manager_id, [...(reports.get(p.manager_id) ?? []), p]);
        }
        return {
            ceo: people.find((p) => p.is_co_ceo) ?? null,
            top: people.filter((p) => !p.manager_id),
            reports,
            depts: new Map((org?.departments ?? []).map((d) => [d.id, d.name])),
        };
    }, [org]);

    return (
        <>
            <header className="page-head">
                <div>
                    <h1>Organisation</h1>
                    <p className="lede">
                        You are the CEO. Your co-CEO runs the company day to day: give it a goal and
                        it hands the work down. Anything that costs money waits for you.
                    </p>
                </div>
                {!hiring && (
                    <button type="button" onClick={() => setHiring(true)}>
                        Hire
                    </button>
                )}
            </header>
            <ErrorLine error={error} />
            {org && hiring && (
                <HireForm
                    org={org}
                    onDone={() => {
                        setHiring(false);
                        load();
                    }}
                />
            )}
            {org && <HireRequests org={org} onDone={load} />}
            {ceo ? (
                <section className="section" aria-labelledby="goal">
                    <h2 id="goal">Give the company a goal</h2>
                    <GoalBox ceo={ceo} />
                </section>
            ) : (
                org && (
                    <div className="rows empty">
                        No co-CEO yet. Hire one first: it runs the company for you, hands work down
                        and runs the standups.
                    </div>
                )
            )}
            <section className="section" aria-labelledby="chart">
                <h2 id="chart">Who reports to whom</h2>
                <div className="org-chart" aria-busy={org === null}>
                    <div className="node node-you">
                        <span className="task-title">You</span>
                        <span className="meta">CEO</span>
                    </div>
                    {top.length > 0 ? (
                        <ul>
                            {top.map((p) => (
                                <Node key={p.id} p={p} reports={reports} depts={depts} />
                            ))}
                        </ul>
                    ) : (
                        <p className="meta">Nobody yet.</p>
                    )}
                </div>
            </section>
            <section className="section" aria-labelledby="depts">
                <h2 id="depts">Departments</h2>
                {org && org.departments.length > 0 && (
                    <ul className="rows">
                        {org.departments.map((d) => (
                            <li key={d.id} className="dept-row">
                                <span className="task-title">{d.name}</span>
                                <span className="meta">
                                    {d.head
                                        ? `Headed by ${org.people.find((p) => p.id === d.head)?.name ?? d.head}`
                                        : "No head yet"}
                                    , {org.people.filter((p) => p.department_id === d.id).length}{" "}
                                    people
                                </span>
                            </li>
                        ))}
                    </ul>
                )}
                <DepartmentForm onDone={load} />
            </section>
        </>
    );
}
