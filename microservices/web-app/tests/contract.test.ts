// The OpenAPI document is the contract: every route file is in it, and everything in it has a
// route file with that method.
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import spec from "@/contracts/openapi.json";

const API = path.join(__dirname, "..", "app", "api", "v1");
const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

function routes(dir: string, prefix = "/api/v1"): string[] {
    const out: string[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            const seg = entry.name.replace(/^\[(.+)\]$/, "{$1}");
            out.push(...routes(full, `${prefix}/${seg}`));
        } else if (entry.name === "route.ts") {
            const src = readFileSync(full, "utf8");
            for (const m of METHODS) {
                if (new RegExp(`export (async )?function ${m}\\b`).test(src)) {
                    out.push(`${m.toLowerCase()} ${prefix}`);
                }
            }
        }
    }
    return out;
}

describe("the OpenAPI contract", () => {
    const implemented = routes(API).sort();
    const documented = Object.entries(spec.paths as Record<string, Record<string, unknown>>)
        .flatMap(([p, ops]) =>
            Object.keys(ops)
                .filter((k) => k !== "parameters")
                .map((m) => `${m} ${p}`),
        )
        .sort();

    it("documents exactly the routes that exist", () => {
        expect(implemented).toEqual(documented);
    });

    it("asks for an Idempotency-Key on every create and every human answer", () => {
        const needsKey = [
            "createTask",
            "decideApproval",
            "replyToTask",
            "giveFeedback",
            "correctMemory",
        ];
        for (const ops of Object.values(spec.paths as Record<string, Record<string, unknown>>)) {
            for (const op of Object.values(ops) as {
                operationId?: string;
                parameters?: { name: string }[];
            }[]) {
                if (op.operationId && needsKey.includes(op.operationId)) {
                    expect(op.parameters?.map((p) => p.name)).toContain("Idempotency-Key");
                }
            }
        }
    });

    it("describes every error the same way", () => {
        const get = (
            spec.paths as Record<string, Record<string, { responses: Record<string, unknown> }>>
        )["/api/v1/tasks/{id}"]!.get!;
        expect(Object.keys(get.responses)).toEqual(
            expect.arrayContaining(["200", "400", "404", "503"]),
        );
    });
});
