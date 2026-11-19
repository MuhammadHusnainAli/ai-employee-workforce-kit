import type { Metadata } from "next";
import { type Org, OrgView } from "@/components/org";
import { realBackends } from "@/lib/api-client/grpc";
import { firstRead, pageReader } from "@/lib/api/http";
import { getOrg } from "@/lib/api/org";

export const metadata: Metadata = { title: "Organisation" };
export const dynamic = "force-dynamic";

export default async function OrgPage() {
    const first = await firstRead(() => getOrg(realBackends(), pageReader()));
    return <OrgView initial={(first as unknown as Org) ?? null} />;
}
