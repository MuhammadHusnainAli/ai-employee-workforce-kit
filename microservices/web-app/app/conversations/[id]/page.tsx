import type { Metadata } from "next";
import { ConversationView } from "@/components/conversations";

export const metadata: Metadata = { title: "Conversation" };
export const dynamic = "force-dynamic";

export default async function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
    const { id } = await params;
    return <ConversationView id={id} />;
}
