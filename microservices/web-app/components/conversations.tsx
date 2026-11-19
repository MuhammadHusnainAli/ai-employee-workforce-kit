"use client";
// Every conversation in the company: direct messages, calls between employees, standups. You can
// read all of them, message anyone, and join a conversation that is still open.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, when } from "./api";
import { ErrorLine } from "./bits";

export interface Message {
    seq: number;
    author: string;
    author_kind: "employee" | "person" | "system";
    kind: string;
    text: string;
    created_at: string | null;
}

export interface Conversation {
    id: string;
    kind: "direct" | "call" | "standup";
    topic: string;
    status: "open" | "closed";
    participants: { id: string; kind: string }[];
    turns: number;
    max_turns: number;
    last_seq: number;
    last_message: string | null;
    last_author: string | null;
    updated_at: string | null;
}

const KIND_TEXT = { direct: "Message", call: "Call", standup: "Standup" } as const;
const FILTERS = [
    { id: "", label: "All" },
    { id: "direct", label: "Messages" },
    { id: "call", label: "Calls" },
    { id: "standup", label: "Standups" },
];
const POLL_MS = 2_000;

const who = (c: Conversation) =>
    c.participants.map((p) => (p.kind === "person" ? "you" : p.id)).join(", ");

function MessageTo({ onSent }: { onSent: () => void }) {
    const [people, setPeople] = useState<{ id: string; name: string }[]>([]);
    const [to, setTo] = useState("");
    const [text, setText] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [key, setKey] = useState(() => crypto.randomUUID());
    useEffect(() => {
        api<{ people: { id: string; name: string }[] }>("/org")
            .then((o) => {
                setPeople(o.people);
                setTo((t) => t || o.people[0]?.id || "");
            })
            .catch(() => setPeople([]));
    }, []);
    async function submit(e: React.FormEvent) {
        e.preventDefault();
        setError(null);
        try {
            await api("/conversations", { method: "POST", key, body: { to, text: text.trim() } });
            setText("");
            setKey(crypto.randomUUID());
            onSent();
        } catch (err) {
            setError((err as Error).message);
        }
    }
    if (!people.length) return null;
    return (
        <form className="compose" onSubmit={submit}>
            <label>
                <span>Message</span>
                <select value={to} onChange={(e) => setTo(e.target.value)}>
                    {people.map((p) => (
                        <option key={p.id} value={p.id}>
                            {p.name}
                        </option>
                    ))}
                </select>
            </label>
            <label>
                <span>Text</span>
                <input value={text} onChange={(e) => setText(e.target.value)} required />
            </label>
            <button type="submit" disabled={!text.trim() || !to}>
                Send
            </button>
            <ErrorLine error={error} />
        </form>
    );
}

export function ConversationsView({ participant }: { participant: string }) {
    const [kind, setKind] = useState("");
    const [list, setList] = useState<Conversation[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(() => {
        const q = new URLSearchParams();
        if (kind) q.set("kind", kind);
        if (participant) q.set("participant", participant);
        api<{ conversations: Conversation[] }>(`/conversations?${q}`)
            .then((r) => {
                setList(r.conversations);
                setError(null);
            })
            .catch((e: Error) => setError(e.message));
    }, [kind, participant]);
    useEffect(() => {
        load();
        const t = setInterval(load, POLL_MS * 3);
        return () => clearInterval(t);
    }, [load]);

    return (
        <>
            <header className="page-head">
                <div>
                    <h1>Conversations</h1>
                    <p className="lede">
                        What your employees say to each other and to you: messages, calls and
                        standups. {participant ? `Showing ${participant}'s.` : ""}
                    </p>
                </div>
            </header>
            <MessageTo onSent={load} />
            <div className="tabs" role="tablist" aria-label="Kind">
                {FILTERS.map((f) => (
                    <button
                        key={f.id}
                        type="button"
                        role="tab"
                        aria-selected={kind === f.id}
                        onClick={() => setKind(f.id)}
                    >
                        {f.label}
                    </button>
                ))}
            </div>
            <ErrorLine error={error} />
            {list && list.length === 0 ? (
                <div className="rows empty">No conversations yet.</div>
            ) : (
                <ul className="rows" aria-busy={list === null}>
                    {(list ?? []).map((c) => (
                        <li key={c.id}>
                            <Link href={`/conversations/${c.id}`} className="task-row">
                                <span className="task-title">
                                    {c.kind === "direct" ? who(c) : c.topic || who(c)}
                                </span>
                                <span className="chip">
                                    {KIND_TEXT[c.kind]}
                                    {c.status === "closed" ? ", closed" : ""}
                                </span>
                                <span className="meta">
                                    {c.last_message
                                        ? `${c.last_author}: ${c.last_message.slice(0, 140)}`
                                        : "Nothing said yet"}
                                </span>
                                <span className="meta">{when(c.updated_at)}</span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </>
    );
}

/** The messages of one conversation, kept up to date, with a box to speak in while it is open. */
export function Thread({
    id,
    kinds,
    onUpdate,
}: {
    id: string;
    kinds?: string[];
    onUpdate?: (c: Conversation) => void;
}) {
    const [convo, setConvo] = useState<Conversation | null>(null);
    const [messages, setMessages] = useState<Message[]>([]);
    const [text, setText] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [key, setKey] = useState(() => crypto.randomUUID());
    const last = useRef(0);

    const poll = useCallback(() => {
        api<{ conversation: Conversation; messages: Message[] }>(
            `/conversations/${id}?after=${last.current}`,
        )
            .then((r) => {
                setConvo(r.conversation);
                onUpdate?.(r.conversation);
                if (r.messages.length) {
                    last.current = r.messages[r.messages.length - 1]!.seq;
                    setMessages((m) => [...m, ...r.messages]);
                }
                setError(null);
            })
            .catch((e: Error) => setError(e.message));
    }, [id, onUpdate]);
    useEffect(() => {
        poll();
        const t = setInterval(poll, POLL_MS);
        return () => clearInterval(t);
    }, [poll]);

    async function send(e: React.FormEvent) {
        e.preventDefault();
        setError(null);
        try {
            await api(`/conversations/${id}/messages`, {
                method: "POST",
                key,
                body: { text: text.trim() },
            });
            setText("");
            setKey(crypto.randomUUID());
            poll();
        } catch (err) {
            setError((err as Error).message);
        }
    }

    const shown = kinds ? messages.filter((m) => kinds.includes(m.kind)) : messages;
    return (
        <div className="thread">
            <ol className="messages" aria-live="polite">
                {shown.map((m) => (
                    <li key={m.seq} className={`msg msg-${m.author_kind}`}>
                        <span className="msg-head">
                            <strong>
                                {m.author_kind === "person" ? `${m.author} (you)` : m.author}
                            </strong>
                            {m.kind !== "message" && <span className="chip">{m.kind}</span>}
                            <span className="meta">{when(m.created_at)}</span>
                        </span>
                        <p className="prose">{m.text}</p>
                    </li>
                ))}
                {convo && !shown.length && <li className="meta">Nothing said yet.</li>}
            </ol>
            <ErrorLine error={error} />
            {convo?.status === "open" ? (
                <form className="say" onSubmit={send}>
                    <label>
                        <span>Join in</span>
                        <textarea
                            value={text}
                            onChange={(e) => setText(e.target.value)}
                            rows={2}
                            required
                        />
                    </label>
                    <button type="submit" disabled={!text.trim()}>
                        Say it
                    </button>
                </form>
            ) : (
                convo && <p className="meta">This conversation is closed.</p>
            )}
        </div>
    );
}

export function ConversationView({ id }: { id: string }) {
    const [convo, setConvo] = useState<Conversation | null>(null);
    return (
        <>
            <header className="page-head">
                <div>
                    <p className="meta">
                        <Link href="/conversations">Conversations</Link>
                    </p>
                    <h1>{convo ? convo.topic || who(convo) : "Conversation"}</h1>
                    {convo && (
                        <p className="lede">
                            {KIND_TEXT[convo.kind]} between {who(convo)}
                            {convo.kind === "call" && convo.max_turns
                                ? `, ${convo.turns} of ${convo.max_turns} questions`
                                : ""}
                            .
                        </p>
                    )}
                </div>
                {convo?.kind === "standup" && (
                    <Link href={`/standups/${id}`}>Open the standup</Link>
                )}
            </header>
            <Thread id={id} onUpdate={setConvo} />
        </>
    );
}
