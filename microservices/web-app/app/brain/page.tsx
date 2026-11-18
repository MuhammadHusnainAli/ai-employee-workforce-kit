import type { Metadata } from "next";
import { BrainView } from "@/components/brain";
import type { Memory } from "@/components/api";
import { realBackends } from "@/lib/api-client/grpc";
import { employee as checkEmployee, firstRead, pageReader } from "@/lib/api/http";
import { listMemories } from "@/lib/api/brain";

export const metadata: Metadata = { title: "Brain" };
export const dynamic = "force-dynamic";

export default async function BrainPage({
    searchParams,
}: {
    searchParams: Promise<{ employee?: string }>;
}) {
    const { employee: asked = "summariser" } = await searchParams;
    let employee = "summariser";
    try {
        employee = checkEmployee(asked);
    } catch {
        /* a bad id in the URL opens the default */
    }
    const first = await firstRead(() =>
        listMemories(
            realBackends(),
            pageReader(),
            employee,
            new URLSearchParams("status=active&page_size=100"),
        ),
    );
    return (
        <BrainView initial={(first?.memories as unknown as Memory[]) ?? null} preset={employee} />
    );
}
