"use client";
// The places, with a live count of tasks waiting for a person.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type Task } from "./api";
import { useTaskEvents } from "./live";

export function NavLinks() {
    const path = usePathname();
    const [waiting, setWaiting] = useState<number | null>(null);
    const refresh = useCallback(() => {
        api<{ tasks: Task[] }>("/tasks?status=waiting_human&limit=200")
            .then((r) => setWaiting(r.tasks.length))
            .catch(() => setWaiting(null));
    }, []);
    useEffect(refresh, [refresh]);
    useTaskEvents((e) => {
        if (e.type === "task.state_changed") refresh();
    });

    const links = [
        { href: "/org", label: "Organisation" },
        { href: "/employees", label: "Employees" },
        { href: "/tasks", label: "Tasks" },
        { href: "/approvals", label: "Waiting for you", count: waiting },
        { href: "/conversations", label: "Conversations" },
        { href: "/standups", label: "Standups" },
        { href: "/brain", label: "Brain" },
    ];
    return (
        <nav aria-label="Main">
            <ul className="nav">
                {links.map((l) => {
                    const active = path === l.href || path.startsWith(`${l.href}/`);
                    return (
                        <li key={l.href}>
                            <Link href={l.href} aria-current={active ? "page" : undefined}>
                                {l.label}
                                {l.count ? <span className="count">{l.count}</span> : null}
                            </Link>
                        </li>
                    );
                })}
            </ul>
        </nav>
    );
}
