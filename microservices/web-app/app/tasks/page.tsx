import type { Metadata } from "next";
import { TasksView } from "@/components/tasks";
import type { Task } from "@/components/api";
import { realBackends } from "@/lib/api-client/grpc";
import { firstRead, pageReader } from "@/lib/api/http";
import { listTasks } from "@/lib/api/tasks";

export const metadata: Metadata = { title: "Tasks" };
export const dynamic = "force-dynamic";

export default async function TasksPage() {
    const first = await firstRead(() =>
        listTasks(realBackends(), pageReader(), new URLSearchParams("limit=100")),
    );
    return <TasksView initial={(first?.tasks as unknown as Task[]) ?? null} />;
}
