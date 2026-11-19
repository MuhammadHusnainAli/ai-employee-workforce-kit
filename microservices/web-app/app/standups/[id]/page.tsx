import type { Metadata } from "next";
import { StandupRoom } from "@/components/standups";

export const metadata: Metadata = { title: "Standup" };
export const dynamic = "force-dynamic";

export default async function StandupPage({ params }: { params: Promise<{ id: string }> }) {
    const { id } = await params;
    return <StandupRoom id={id} />;
}
