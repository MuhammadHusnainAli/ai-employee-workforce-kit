"use client";
// One task: what was asked, what the employee did step by step (live), what it answered, and
// the controls a person needs when the run waits for them.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Run, type Step, type Task, when } from "./api";
import { ErrorLine, Status } from "./bits";
import { useTaskEvents } from "./live";

const KIND_TEXT: Record<string, string> = {
    tool_calls: "Used tools",
    final: "Answered",
    ask_human: "Asked a question",
    approval: "Asked for approval",
    cancelled: "Stopped",
    error: "Hit an error",
};

const REASON_TEXT: Record<string, string> = {
    approval: "It needs your approval before it does something risky.",
    loop: "It seems to be going in circles and wants guidance.",
    budget: "It reached its spending limit for this task.",
    guardrail: "A safety check stopped it; it needs a person to decide.",
};

function StepRail({ steps, fresh }: { steps: Step[]; fresh: number | null }) {
    if (steps.length === 0) {
        return <p className="meta">No steps yet. They appear here as the employee works.</p>;
    }
    return (
        <ol className="steps" aria-label="Steps">
            {steps.map((s) => (
                <li
                    key={s.seq}
                    className="step"
                    data-status={s.status_after}
                    data-new={s.seq === fresh ? "true" : undefined}
                >
                    <div className="step-head">
                        <span className="step-kind">{KIND_TEXT[s.kind] ?? s.kind}</span>
                        <span className="meta">
                            step {s.step}, {s.input_tokens + s.output_tokens} tokens, ${s.cost_usd}
                            {s.memory_degraded ? ", without full memory" : ""}
                        </span>
                        <span className="meta">{when(s.created_at)}</span>
                    </div>
                    {(s.decision || s.results.length > 0) && (
                        <details>
                            <summary>What it decided</summary>
                            <pre>
                                {JSON.stringify(
                                    { decision: s.decision, results: s.results },
                                    null,
                                    2,
                                )}
                            </pre>
                        </details>
                    )}
                </li>
            ))}
        </ol>
    );
}

function Answer({ task, onDone }: { task: Task; onDone: () => void }) {
    const [note, setNote] = useState("");
    const [reply, setReply] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const isApproval = task.stop_reason === "approval";

    async function send(path: string, body: unknown) {
        setBusy(true);
        setError(null);
        try {
            await api(`/tasks/${task.id}/${path}`, { method: "POST", body });
            onDone();
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <section className="ask section" aria-labelledby="ask-title">
            <h2 id="ask-title">{isApproval ? "Approve this?" : "It needs your answer"}</h2>
            <p className="prose">
                {REASON_TEXT[task.stop_reason] ??
                    "The employee stopped and is waiting for a person."}
            </p>
            {isApproval ? (
                <>
                    <label>
                        <span>Note for the employee (optional)</span>
                        <input value={note} onChange={(e) => setNote(e.target.value)} />
                    </label>
                    <div className="row-actions">
                        <button
                            disabled={busy}
                            onClick={() => send("approval", { approved: true, note })}
                        >
                            Approve
                        </button>
                        <button
                            className="quiet"
                            disabled={busy}
                            onClick={() => send("approval", { approved: false, note })}
                        >
                            Decline
                        </button>
                    </div>
                </>
            ) : (
                <>
                    <label>
                        <span>Your reply</span>
                        <textarea
                            value={reply}
                            onChange={(e) => setReply(e.target.value)}
                            rows={3}
                        />
                    </label>
                    <div className="row-actions">
                        <button
                            disabled={busy || !reply.trim()}
                            onClick={() => send("reply", { text: reply })}
                        >
                            Send reply
                        </button>
                    </div>
                </>
            )}
            <ErrorLine error={error} />
        </section>
    );
}

export function TaskDetail({
    id,
    initial,
}: {
    id: string;
    initial: { task: Task; run: Run | null; steps: Step[] } | null;
}) {
    const [task, setTask] = useState<Task | null>(initial?.task ?? null);
    const [run, setRun] = useState<Run | null>(initial?.run ?? null);
    const [steps, setSteps] = useState<Step[]>(initial?.steps ?? []);
    const [fresh, setFresh] = useState<number | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [cancelling, setCancelling] = useState(false);
    const known = useRef(initial?.steps.length ?? 0);

    const load = useCallback(async () => {
        try {
            const [t, s] = await Promise.all([
                api<{ task: Task; run: Run | null }>(`/tasks/${id}`),
                api<{ steps: Step[] }>(`/tasks/${id}/steps?page_size=200`),
            ]);
            setTask(t.task);
            setRun(t.run);
            const last = s.steps.at(-1)?.seq ?? null;
            // Highlight only a step that arrived while you were watching.
            if (last !== null && known.current > 0 && s.steps.length > known.current)
                setFresh(last);
            known.current = s.steps.length;
            setSteps(s.steps);
            setError(null);
        } catch (e) {
            setError((e as Error).message);
        }
    }, [id]);

    useEffect(() => {
        void load();
    }, [load]);
    useTaskEvents((e) => {
        if (e.task_id === id) void load();
    });

    async function cancel() {
        setCancelling(true);
        try {
            await api(`/tasks/${id}/cancel`, {
                method: "POST",
                body: { reason: "Stopped from the web app" },
            });
            await load();
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setCancelling(false);
        }
    }

    if (!task) {
        return error ? <ErrorLine error={error} /> : <p className="meta">Loading the task.</p>;
    }
    const finished = ["completed", "failed", "cancelled"].includes(task.status);

    return (
        <>
            <p className="meta">
                <Link href="/tasks">Tasks</Link>
            </p>
            <header className="page-head">
                <div>
                    <h1>{task.title || "Task"}</h1>
                    <p className="lede">
                        For {task.employee_id}, assigned by {task.created_by}{" "}
                        {when(task.created_at)}
                    </p>
                </div>
                <Status value={task.status} />
            </header>
            <ErrorLine error={error} />
            <div className="detail">
                <div>
                    {task.status === "waiting_human" && <Answer task={task} onDone={load} />}
                    {run?.output && (
                        <section className="section" aria-labelledby="answer-title">
                            <h2 id="answer-title">The answer</h2>
                            <div className="answer">
                                <p className="prose">{run.output}</p>
                            </div>
                        </section>
                    )}
                    <section className="section" aria-labelledby="steps-title">
                        <h2 id="steps-title">What it did</h2>
                        <StepRail steps={steps} fresh={fresh} />
                    </section>
                </div>
                <aside>
                    <section className="section" aria-labelledby="asked-title">
                        <h2 id="asked-title">What was asked</h2>
                        <p className="prose">{task.input}</p>
                    </section>
                    <dl className="facts">
                        <dt>Priority</dt>
                        <dd>{task.priority}</dd>
                        {run && (
                            <>
                                <dt>Tokens</dt>
                                <dd>{run.input_tokens + run.output_tokens}</dd>
                                <dt>Cost</dt>
                                <dd>${run.cost_usd}</dd>
                            </>
                        )}
                        {task.stop_reason && (
                            <>
                                <dt>Stopped because</dt>
                                <dd>{task.stop_reason}</dd>
                            </>
                        )}
                        <dt>Task id</dt>
                        <dd>{task.id}</dd>
                    </dl>
                    {!finished && (
                        <p style={{ marginTop: "1.5rem" }}>
                            <button className="danger" onClick={cancel} disabled={cancelling}>
                                {cancelling ? "Stopping" : "Stop this task"}
                            </button>
                        </p>
                    )}
                </aside>
            </div>
        </>
    );
}
