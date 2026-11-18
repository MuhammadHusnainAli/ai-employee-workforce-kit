"use client";
// Employees: create one, see who it is and what it may do, change it (a new version), pause it.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, when } from "./api";
import { ErrorLine } from "./bits";

export interface Employee {
    id: string;
    name: string;
    status: "active" | "paused";
    active_version: number;
    created_at: string | null;
}

interface Version {
    version: number;
    role: string;
    instructions: string;
    model: string;
    tools: { tool: string; tier: string }[];
    max_steps: number | null;
    max_cost_usd: string | null;
    created_by: string;
    created_at: string | null;
}

interface Detail {
    employee: Employee;
    active_version: Version | null;
}

/** What agent-runtime can run today; tools that reach outside arrive with integrations. */
const TOOLS = [
    { name: "word_count", what: "Count the words in a text" },
    { name: "char_count", what: "Count the characters in a text" },
    { name: "current_time", what: "Tell the current date and time" },
];
const MODELS = [
    { id: "local-mock", label: "Local test model (no key needed)" },
    { id: "claude-haiku", label: "Claude Haiku" },
    { id: "claude-sonnet", label: "Claude Sonnet" },
];

const slug = (name: string) =>
    name
        .toLowerCase()
        .normalize("NFKD")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .replace(/^([^a-z])/, "e-$1")
        .slice(0, 63);

interface Draft {
    name: string;
    id: string;
    role: string;
    instructions: string;
    model: string;
    tools: string[];
    max_steps: number;
    max_cost_usd: string;
}

const EMPTY: Draft = {
    name: "",
    id: "",
    role: "",
    instructions: "",
    model: "local-mock",
    tools: ["word_count"],
    max_steps: 10,
    max_cost_usd: "0.50",
};

function EmployeeForm({
    start,
    editing,
    onDone,
    onCancel,
}: {
    start: Draft;
    editing: boolean;
    onDone: () => void;
    onCancel: () => void;
}) {
    const [d, setD] = useState<Draft>(start);
    const [idTouched, setIdTouched] = useState(editing);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // One key per draft: a double click or a retry publishes once.
    const [key] = useState(() => crypto.randomUUID());
    const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));

    async function submit(e: React.FormEvent) {
        e.preventDefault();
        setBusy(true);
        setError(null);
        const version = {
            role: d.role.trim(),
            instructions: d.instructions.trim(),
            model: d.model,
            tools: d.tools.map((tool) => ({ tool, tier: "R0" })),
            max_steps: d.max_steps,
            max_cost_usd: d.max_cost_usd.trim(),
        };
        try {
            if (editing) {
                await api(`/employees/${d.id}/versions`, { method: "POST", key, body: version });
            } else {
                await api("/employees", {
                    method: "POST",
                    key,
                    body: { id: d.id, name: d.name.trim(), ...version },
                });
            }
            onDone();
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <form className="employee-form" onSubmit={submit}>
            <h2>{editing ? `Change ${d.name}` : "New employee"}</h2>
            {!editing && (
                <div className="two">
                    <label>
                        <span>Name</span>
                        <input
                            value={d.name}
                            onChange={(e) => {
                                set("name", e.target.value);
                                if (!idTouched) set("id", slug(e.target.value));
                            }}
                            placeholder="Ana from Support"
                            required
                        />
                    </label>
                    <label>
                        <span>ID (used in tasks and the API)</span>
                        <input
                            value={d.id}
                            onChange={(e) => {
                                setIdTouched(true);
                                set("id", e.target.value);
                            }}
                            pattern="[a-z][a-z0-9-]{1,62}"
                            title="Lower-case letters, digits and '-', starting with a letter"
                            required
                        />
                    </label>
                </div>
            )}
            <label>
                <span>Role: what is this employee for?</span>
                <input
                    value={d.role}
                    onChange={(e) => set("role", e.target.value)}
                    placeholder="Answers billing questions from Acme's customers."
                    required
                />
            </label>
            <label>
                <span>Instructions: how should it work?</span>
                <textarea
                    value={d.instructions}
                    onChange={(e) => set("instructions", e.target.value)}
                    rows={5}
                    placeholder="Be brief and friendly. Check the order before answering. Never promise refunds outside the 30-day policy."
                />
            </label>
            <div className="two">
                <label>
                    <span>Model</span>
                    <select value={d.model} onChange={(e) => set("model", e.target.value)}>
                        {MODELS.map((m) => (
                            <option key={m.id} value={m.id}>
                                {m.label}
                            </option>
                        ))}
                    </select>
                </label>
                <div className="two">
                    <label>
                        <span>Most steps per task</span>
                        <input
                            type="number"
                            min={1}
                            max={100}
                            value={d.max_steps}
                            onChange={(e) => set("max_steps", Number(e.target.value))}
                        />
                    </label>
                    <label>
                        <span>Most spend per task ($)</span>
                        <input
                            value={d.max_cost_usd}
                            onChange={(e) => set("max_cost_usd", e.target.value)}
                            inputMode="decimal"
                        />
                    </label>
                </div>
            </div>
            <fieldset className="tools">
                <legend>Tools it may use</legend>
                {TOOLS.map((t) => (
                    <label key={t.name} className="check">
                        <input
                            type="checkbox"
                            checked={d.tools.includes(t.name)}
                            onChange={(e) =>
                                set(
                                    "tools",
                                    e.target.checked
                                        ? [...d.tools, t.name]
                                        : d.tools.filter((x) => x !== t.name),
                                )
                            }
                        />
                        {t.what}
                    </label>
                ))}
                <p className="meta">
                    Email, calendars and other systems arrive with integrations. Tools that change
                    things outside will ask for your approval.
                </p>
            </fieldset>
            <ErrorLine error={error} />
            <div className="row-actions">
                <button type="submit" disabled={busy}>
                    {editing ? "Publish the change" : "Create employee"}
                </button>
                <button type="button" className="quiet" onClick={onCancel}>
                    Cancel
                </button>
            </div>
            {editing && (
                <p className="meta">Tasks already running keep the version they started with.</p>
            )}
        </form>
    );
}

function EmployeeRow({
    e,
    onChange,
    onEdit,
}: {
    e: Employee;
    onChange: () => void;
    onEdit: (d: Detail) => void;
}) {
    const [detail, setDetail] = useState<Detail | null>(null);
    const [open, setOpen] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!open || detail) return;
        api<Detail>(`/employees/${e.id}`)
            .then(setDetail)
            .catch((err: Error) => setError(err.message));
    }, [open, detail, e.id]);

    async function toggle() {
        setError(null);
        try {
            await api(`/employees/${e.id}/${e.status === "paused" ? "resume" : "pause"}`, {
                method: "POST",
                body: {},
            });
            onChange();
        } catch (err) {
            setError((err as Error).message);
        }
    }

    const v = detail?.active_version;
    return (
        <li>
            <button
                type="button"
                className="employee-head"
                aria-expanded={open}
                onClick={() => setOpen(!open)}
            >
                <span className="task-title">{e.name}</span>
                <span className={e.status === "paused" ? "chip chip-held" : "chip"}>
                    {e.status === "paused" ? "Paused" : "Working"}
                </span>
                <span className="meta">
                    {e.id}, version {e.active_version || "none yet"}, created {when(e.created_at)}
                </span>
            </button>
            {open && (
                <div className="employee-body">
                    <ErrorLine error={error} />
                    {v ? (
                        <dl className="facts">
                            <dt>Role</dt>
                            <dd>{v.role}</dd>
                            <dt>Instructions</dt>
                            <dd className="prose">{v.instructions || "None"}</dd>
                            <dt>Model</dt>
                            <dd>{v.model}</dd>
                            <dt>Tools</dt>
                            <dd>
                                {v.tools.length ? v.tools.map((t) => t.tool).join(", ") : "None"}
                            </dd>
                            <dt>Limits</dt>
                            <dd>
                                {v.max_steps ?? "default"} steps, ${v.max_cost_usd ?? "default"} per
                                task
                            </dd>
                            <dt>Changed</dt>
                            <dd>
                                by {v.created_by} {when(v.created_at)}
                            </dd>
                        </dl>
                    ) : (
                        !error && <p className="meta">Loading.</p>
                    )}
                    <div className="row-actions">
                        <Link href={`/tasks?employee=${e.id}`}>Give it a task</Link>
                        <Link href={`/brain?employee=${e.id}`}>See what it knows</Link>
                        {detail && (
                            <button type="button" className="quiet" onClick={() => onEdit(detail)}>
                                Change
                            </button>
                        )}
                        <button type="button" className="quiet" onClick={toggle}>
                            {e.status === "paused" ? "Resume" : "Pause"}
                        </button>
                    </div>
                </div>
            )}
        </li>
    );
}

export function EmployeesView({ initial }: { initial: Employee[] | null }) {
    const [employees, setEmployees] = useState<Employee[] | null>(initial);
    const [form, setForm] = useState<{ start: Draft; editing: boolean } | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [version, setVersion] = useState(0); // remounts rows after a change

    const load = useCallback(() => {
        api<{ employees: Employee[] }>("/employees")
            .then((r) => {
                setEmployees(r.employees);
                setError(null);
            })
            .catch((e: Error) => setError(e.message));
    }, []);
    useEffect(load, [load]);

    const done = () => {
        setForm(null);
        setVersion((n) => n + 1);
        load();
    };

    return (
        <>
            <header className="page-head">
                <div>
                    <h1>Employees</h1>
                    <p className="lede">
                        Who works for you: what each one is for, how it should work, and what it may
                        do on its own.
                    </p>
                </div>
                {!form && (
                    <button type="button" onClick={() => setForm({ start: EMPTY, editing: false })}>
                        New employee
                    </button>
                )}
            </header>
            {form && (
                <EmployeeForm
                    start={form.start}
                    editing={form.editing}
                    onDone={done}
                    onCancel={() => setForm(null)}
                />
            )}
            <ErrorLine error={error} />
            {employees && employees.length === 0 ? (
                <div className="rows empty">
                    No employees yet. Create the first one: give it a role, instructions and the
                    tools it may use.
                </div>
            ) : (
                <ul className="rows" key={version} aria-busy={employees === null}>
                    {(employees ?? []).map((e) => (
                        <EmployeeRow
                            key={e.id}
                            e={e}
                            onChange={done}
                            onEdit={(dt) => {
                                const v = dt.active_version;
                                setForm({
                                    editing: true,
                                    start: {
                                        name: dt.employee.name,
                                        id: dt.employee.id,
                                        role: v?.role ?? "",
                                        instructions: v?.instructions ?? "",
                                        model: v?.model ?? "local-mock",
                                        tools: v?.tools.map((t) => t.tool) ?? [],
                                        max_steps: v?.max_steps ?? 10,
                                        max_cost_usd: v?.max_cost_usd ?? "0.50",
                                    },
                                });
                                window.scrollTo({ top: 0, behavior: "smooth" });
                            }}
                        />
                    ))}
                </ul>
            )}
        </>
    );
}
