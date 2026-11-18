import type { Metadata } from "next";
import { ApprovalsView } from "@/components/approvals";
import type { Task } from "@/components/api";
import { realBackends } from "@/lib/api-client/grpc";
import { firstRead, pageReader } from "@/lib/api/http";
import { listTasks } from "@/lib/api/tasks";

export const metadata: Metadata = { title: "Waiting for you" };
export const dynamic = "force-dynamic";

export default async function ApprovalsPage() {
    const first = await firstRead(() =>
        listTasks(
            realBackends(),
            pageReader(),
            new URLSearchParams("status=waiting_human&limit=200"),
        ),
    );
    const oldestFirst = first ? [...(first.tasks as unknown as Task[])].reverse() : null;
    return <ApprovalsView initial={oldestFirst} />;
}
