"use client";
// The Brain Inspector: what an employee knows and why, what is held for review, the rules it
// follows, and corrections that become rules once a person approves them.

import { useCallback, useEffect, useState } from "react";
import { api, type Memory, type Proposal, type Rule, when } from "./api";
import { ErrorLine, Trust } from "./bits";

type Tab = "memories" | "held" | "proposals" | "rules" | "teach";

const TABS: { id: Tab; label: string }[] = [
    { id: "memories", label: "What it knows" },
    { id: "held", label: "Held for review" },
    { id: "proposals", label: "Proposed rules" },
    { id: "rules", label: "Rules" },
    { id: "teach", label: "Correct it" },
];

const KIND_TEXT: Record<string, string> = {
    episodic: "Experience",
    semantic: "Fact",
    reflection: "Lesson",
};

const FINDING_TEXT: Record<string, string> = {
    secret: "secret removed",
    email: "email",
    phone: "phone number",
    card: "card number",
    ssn: "ID number",
    iban: "bank account",
    instructions: "tries to give instructions",
};

function source(ref: string | null): string {
    if (!ref) return "Unknown source";
    if (ref.startsWith("inspector:")) return `Corrected by ${ref.slice(10)}`;
    if (ref.startsWith("event:")) return "Learned from a task";
    return ref;
}

function useAction() {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const run = async (fn: () => Promise<unknown>, after: () => void) => {
        setBusy(true);
        setError(null);
        try {
            await fn();
            after();
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setBusy(false);
        }
    };
    return { busy, error, run };
}

function MemoryRow({ m, onChange }: { m: Memory; onChange: () => void }) {
    const [editing, setEditing] = useState(false);
    const [text, setText] = useState(m.content);
    const { busy, error, run } = useAction();
    const findings = Object.entries(m.findings ?? {});

    return (
        <li className="memory" data-status={m.status}>
            {editing ? (
                <label>
                    <span>Correct what it knows</span>
                    <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} />
                </label>
            ) : (
                <p className="memory-text">{m.content}</p>
            )}
            <div className="memory-why">
                <span className="chip">{KIND_TEXT[m.kind] ?? m.kind}</span>
                <Trust value={m.trust} />
                <span className="meta">
                    {source(m.source_ref)} {when(m.recorded_at)}
                </span>
                {m.reinforced > 0 && (
                    <span className="meta">confirmed {m.reinforced} more times</span>
                )}
                {findings.map(([k, n]) => (
                    <span key={k} className={k === "instructions" ? "chip chip-held" : "chip"}>
                        {FINDING_TEXT[k] ?? k}
                        {n > 1 ? ` (${n})` : ""}
                    </span>
                ))}
                {m.valid_to && (
                    <span className="meta">no longer true since {when(m.valid_to)}</span>
                )}
            </div>
            {m.contradicts && (
                <p className="meta">It contradicts something the employee already knows.</p>
            )}
            <div className="row-actions">
                {m.status === "quarantined" ? (
                    <>
                        <button
                            disabled={busy}
                            onClick={() =>
                                run(
                                    () =>
                                        api(`/memories/${m.id}/review`, {
                                            method: "POST",
                                            body: { approve: true },
                                        }),
                                    onChange,
                                )
                            }
                        >
                            Let it use this
                        </button>
                        <button
                            className="quiet"
                            disabled={busy}
                            onClick={() =>
                                run(
                                    () =>
                                        api(`/memories/${m.id}/review`, {
                                            method: "POST",
                                            body: { approve: false },
                                        }),
                                    onChange,
                                )
                            }
                        >
                            Reject
                        </button>
                    </>
                ) : m.status === "active" && !m.valid_to ? (
                    editing ? (
                        <>
                            <button
                                disabled={busy || !text.trim() || text === m.content}
                                onClick={() =>
                                    run(
                                        () =>
                                            api(`/memories/${m.id}/correct`, {
                                                method: "POST",
                                                body: { content: text },
                                            }),
                                        () => {
                                            setEditing(false);
                                            onChange();
                                        },
                                    )
                                }
                            >
                                Save correction
                            </button>
                            <button className="quiet" onClick={() => setEditing(false)}>
                                Keep as is
                            </button>
                        </>
                    ) : (
                        <>
                            <button className="quiet" onClick={() => setEditing(true)}>
                                Correct
                            </button>
                            <button
                                className="danger"
                                disabled={busy}
                                onClick={() => {
                                    if (confirm("Forget this for good? This cannot be undone.")) {
                                        void run(
                                            () => api(`/memories/${m.id}`, { method: "DELETE" }),
                                            onChange,
                                        );
                                    }
                                }}
                            >
                                Forget
                            </button>
                        </>
                    )
                ) : null}
            </div>
            <ErrorLine error={error} />
        </li>
    );
}

function Memories({
    employee,
    held,
    initial = null,
}: {
    employee: string;
    held: boolean;
    initial?: Memory[] | null;
}) {
    const [items, setItems] = useState<Memory[] | null>(initial);
    const [history, setHistory] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const load = useCallback(() => {
        const q = held ? "status=quarantined" : `status=active${history ? "&history=true" : ""}`;
        api<{ memories: Memory[] }>(
            `/employees/${encodeURIComponent(employee)}/memories?${q}&page_size=100`,
        )
            .then((r) => {
                setItems(r.memories);
                setError(null);
            })
            .catch((e: Error) => setError(e.message));
    }, [employee, held, history]);
    useEffect(load, [load]);

    return (
        <>
            {held ? (
                <p className="lede" style={{ marginBottom: "1rem" }}>
                    Things it picked up that it will not use until you decide: text that tries to
                    give it instructions, or that contradicts what a person told it.
                </p>
            ) : (
                <label className="meta" style={{ display: "block", marginBottom: "1rem" }}>
                    <input
                        type="checkbox"
                        checked={history}
                        onChange={(e) => setHistory(e.target.checked)}
                        style={{ width: "auto", marginRight: "0.5rem" }}
                    />
                    Also show what is no longer true
                </label>
            )}
            <ErrorLine error={error} />
            {items && items.length === 0 ? (
                <div className="rows empty">
                    {held
                        ? "Nothing is held for review."
                        : "It has not learned anything yet. Give it a task."}
                </div>
            ) : (
                <ul className="rows" aria-busy={items === null}>
                    {(items ?? []).map((m) => (
                        <MemoryRow key={m.id} m={m} onChange={load} />
                    ))}
                </ul>
            )}
        </>
    );
}

function Proposals({ employee }: { employee: string }) {
    const [items, setItems] = useState<Proposal[] | null>(null);
    const { busy, error, run } = useAction();
    const [loadError, setLoadError] = useState<string | null>(null);
    const load = useCallback(() => {
        api<{ proposals: Proposal[] }>(`/employees/${encodeURIComponent(employee)}/proposals`)
            .then((r) => setItems(r.proposals))
            .catch((e: Error) => setLoadError(e.message));
    }, [employee]);
    useEffect(load, [load]);
    const decide = (id: string, approve: boolean) =>
        run(() => api(`/proposals/${id}/decide`, { method: "POST", body: { approve } }), load);

    return (
        <>
            <p className="lede" style={{ marginBottom: "1rem" }}>
                Corrections become rules only when a person approves them. A high-risk rule needs
                someone other than the person who proposed it.
            </p>
            <ErrorLine error={loadError ?? error} />
            {items && items.length === 0 ? (
                <div className="rows empty">No proposals. Corrections you make appear here.</div>
            ) : (
                <ul className="rows">
                    {(items ?? []).map((p) => (
                        <li key={p.id} className="memory">
                            <p className="memory-text">{p.text}</p>
                            <div className="memory-why">
                                <span className={p.risk === "high" ? "chip chip-held" : "chip"}>
                                    {p.risk} risk
                                </span>
                                <span className="meta">
                                    proposed by {p.proposed_by} {when(p.created_at)}
                                </span>
                                {p.status !== "pending" && (
                                    <span className="meta">
                                        {p.status} by {p.decided_by}
                                    </span>
                                )}
                            </div>
                            {p.status === "pending" && (
                                <div className="row-actions">
                                    <button disabled={busy} onClick={() => decide(p.id, true)}>
                                        Approve rule
                                    </button>
                                    <button
                                        className="quiet"
                                        disabled={busy}
                                        onClick={() => decide(p.id, false)}
                                    >
                                        Reject
                                    </button>
                                </div>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </>
    );
}

function Rules({ employee }: { employee: string }) {
    const [items, setItems] = useState<Rule[] | null>(null);
    const { busy, error, run } = useAction();
    const load = useCallback(() => {
        api<{ rules: Rule[] }>(`/employees/${encodeURIComponent(employee)}/rules`)
            .then((r) => setItems(r.rules))
            .catch(() => setItems([]));
    }, [employee]);
    useEffect(load, [load]);
    return (
        <>
            <p className="lede" style={{ marginBottom: "1rem" }}>
                It follows these on every task, before anything else it knows.
            </p>
            <ErrorLine error={error} />
            {items && items.length === 0 ? (
                <div className="rows empty">No rules yet.</div>
            ) : (
                <ul className="rows">
                    {(items ?? []).map((r) => (
                        <li key={r.id} className="memory">
                            <p className="memory-text">{r.text}</p>
                            <div className="memory-why">
                                <span className="meta">
                                    approved by {r.approved_by} {when(r.created_at)}
                                </span>
                                <button
                                    className="danger"
                                    disabled={busy}
                                    onClick={() =>
                                        run(
                                            () =>
                                                api(`/rules/${r.id}/revoke`, {
                                                    method: "POST",
                                                    body: {},
                                                }),
                                            load,
                                        )
                                    }
                                >
                                    Stop this rule
                                </button>
                            </div>
                        </li>
                    ))}
                </ul>
            )}
        </>
    );
}

function Teach({ employee, onSent }: { employee: string; onSent: () => void }) {
    const [text, setText] = useState("");
    const { busy, error, run } = useAction();
    return (
        <form
            className="compose"
            style={{ gridTemplateColumns: "1fr auto" }}
            onSubmit={(e) => {
                e.preventDefault();
                void run(
                    () =>
                        api(`/employees/${encodeURIComponent(employee)}/feedback`, {
                            method: "POST",
                            body: { kind: "correction", text },
                        }),
                    () => {
                        setText("");
                        onSent();
                    },
                );
            }}
        >
            <label>
                <span>What should {employee} do differently?</span>
                <textarea
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    placeholder="Always copy the account owner on replies to enterprise customers."
                    rows={3}
                    required
                />
            </label>
            <button type="submit" disabled={busy || !text.trim()}>
                Propose rule
            </button>
            <div style={{ gridColumn: "1 / -1" }}>
                <ErrorLine error={error} />
            </div>
        </form>
    );
}

export function BrainView({ initial }: { initial: Memory[] | null }) {
    const [employee, setEmployee] = useState("summariser");
    const [draft, setDraft] = useState("summariser");
    const [tab, setTab] = useState<Tab>("memories");

    return (
        <>
            <header className="page-head">
                <div>
                    <h1>Brain</h1>
                    <p className="lede">
                        What {employee} knows, where it learned it, and the rules it follows. You
                        can correct or remove anything.
                    </p>
                </div>
                <form
                    className="employee-pick"
                    onSubmit={(e) => {
                        e.preventDefault();
                        if (draft.trim()) setEmployee(draft.trim());
                    }}
                >
                    <label>
                        <span>Employee</span>
                        <input value={draft} onChange={(e) => setDraft(e.target.value)} />
                    </label>
                    <button type="submit" className="quiet">
                        Open
                    </button>
                </form>
            </header>
            <div className="tabs" role="tablist" aria-label="Brain">
                {TABS.map((t) => (
                    <button
                        key={t.id}
                        role="tab"
                        aria-selected={tab === t.id}
                        onClick={() => setTab(t.id)}
                        type="button"
                    >
                        {t.label}
                    </button>
                ))}
            </div>
            <div role="tabpanel" key={`${employee}-${tab}`}>
                {tab === "memories" && (
                    <Memories
                        employee={employee}
                        held={false}
                        initial={employee === "summariser" ? initial : null}
                    />
                )}
                {tab === "held" && <Memories employee={employee} held />}
                {tab === "proposals" && <Proposals employee={employee} />}
                {tab === "rules" && <Rules employee={employee} />}
                {tab === "teach" && (
                    <Teach employee={employee} onSent={() => setTab("proposals")} />
                )}
            </div>
        </>
    );
}
