import type { Metadata } from "next";
import { BrainView } from "@/components/brain";
import type { Memory } from "@/components/api";
import { realBackends } from "@/lib/api-client/grpc";
import { firstRead, pageReader } from "@/lib/api/http";
import { listMemories } from "@/lib/api/brain";

export const metadata: Metadata = { title: "Brain" };
export const dynamic = "force-dynamic";

export default async function BrainPage() {
    const first = await firstRead(() =>
        listMemories(
            realBackends(),
            pageReader(),
            "summariser",
            new URLSearchParams("status=active&page_size=100"),
        ),
    );
    return <BrainView initial={(first?.memories as unknown as Memory[]) ?? null} />;
}
