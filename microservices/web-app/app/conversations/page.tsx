import type { Metadata } from "next";
import { ConversationsView } from "@/components/conversations";

export const metadata: Metadata = { title: "Conversations" };
export const dynamic = "force-dynamic";

export default async function ConversationsPage({
    searchParams,
}: {
    searchParams: Promise<{ participant?: string }>;
}) {
    const { participant = "" } = await searchParams;
    return <ConversationsView participant={participant} />;
}
