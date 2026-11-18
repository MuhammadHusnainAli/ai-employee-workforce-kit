import type { Metadata } from "next";
import { TaskDetail } from "@/components/task-detail";
import type { Run, Step, Task } from "@/components/api";
import { realBackends } from "@/lib/api-client/grpc";
import { firstRead, isUuid, pageReader } from "@/lib/api/http";
import { getTask, listSteps } from "@/lib/api/tasks";

export const metadata: Metadata = { title: "Task" };
export const dynamic = "force-dynamic";

export default async function TaskPage({ params }: { params: Promise<{ id: string }> }) {
    const { id } = await params;
    const first = isUuid(id)
        ? await firstRead(async () => {
              const b = realBackends();
              const [t, s] = await Promise.all([
                  getTask(b, pageReader(), id),
                  listSteps(b, pageReader(), id, new URLSearchParams("page_size=200")),
              ]);
              return {
                  task: t.task as unknown as Task,
                  run: t.run as unknown as Run | null,
                  steps: s.steps as unknown as Step[],
              };
          })
        : null;
    return <TaskDetail id={id} initial={first} />;
}
