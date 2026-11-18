import type { Metadata } from "next";
import { type Employee, EmployeesView } from "@/components/employees";
import { realBackends } from "@/lib/api-client/grpc";
import { firstRead, pageReader } from "@/lib/api/http";
import { listEmployees } from "@/lib/api/workforce";

export const metadata: Metadata = { title: "Employees" };
export const dynamic = "force-dynamic";

export default async function EmployeesPage() {
    const first = await firstRead(() => listEmployees(realBackends(), pageReader()));
    return <EmployeesView initial={(first?.employees as unknown as Employee[]) ?? null} />;
}
