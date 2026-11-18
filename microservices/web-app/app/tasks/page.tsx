import type { Metadata } from "next";
import { TasksView } from "@/components/tasks";
import type { Task } from "@/components/api";
import { realBackends } from "@/lib/api-client/grpc";
import { firstRead, pageReader } from "@/lib/api/http";
import { listTasks } from "@/lib/api/tasks";
import { listEmployees } from "@/lib/api/workforce";

export const metadata: Metadata = { title: "Tasks" };
export const dynamic = "force-dynamic";

export default async function TasksPage({
    searchParams,
}: {
    searchParams: Promise<{ employee?: string }>;
}) {
    const { employee = "" } = await searchParams;
    const [tasks, people] = await Promise.all([
        firstRead(() => listTasks(realBackends(), pageReader(), new URLSearchParams("limit=100"))),
        firstRead(() => listEmployees(realBackends(), pageReader())),
    ]);
    const employees = (people?.employees ?? []).map((e) => ({
        id: String(e.id),
        name: String(e.name),
        status: e.status,
    }));
    return (
        <TasksView
            initial={(tasks?.tasks as unknown as Task[]) ?? null}
            employees={employees}
            preset={employee}
        />
    );
}
