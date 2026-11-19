import type { Metadata } from "next";
import { type Standup, StandupsView } from "@/components/standups";
import { realBackends } from "@/lib/api-client/grpc";
import { listStandups } from "@/lib/api/collaboration";
import { firstRead, pageReader } from "@/lib/api/http";

export const metadata: Metadata = { title: "Standups" };
export const dynamic = "force-dynamic";

export default async function StandupsPage() {
    const first = await firstRead(() => listStandups(realBackends(), pageReader()));
    return <StandupsView initial={(first?.standups as unknown as Standup[]) ?? null} />;
}
