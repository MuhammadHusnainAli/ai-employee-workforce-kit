// Licence policy (ADR-0007): every production dependency must carry an OSI-approved licence.
// Run: npm run licenses
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

const OSI = new Set([
    "MIT",
    "ISC",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "0BSD",
    "MPL-2.0",
    "Unicode-3.0",
    "BlueOak-1.0.0",
    "Zlib",
    "Python-2.0",
    "Unlicense",
    // Weak copyleft, allowed for libraries used unmodified (as in the Python services: psycopg).
    // Here: libvips inside sharp's prebuilt binaries, which Next.js uses for images.
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
]);

// Not code we run but data a dependency ships, reviewed one by one. Keep this short.
const REVIEWED = {
    // Browser usage tables Next.js reads at build time (CC-BY-4.0, attribution only).
    "caniuse-lite": "CC-BY-4.0",
};

const out = execFileSync("npm", ["ls", "--omit=dev", "--all", "--parseable"], {
    encoding: "utf8",
    shell: process.platform === "win32",
});
const dirs = [...new Set(out.split(/\r?\n/).filter((l) => l.includes("node_modules")))];

function licence(meta) {
    const l = meta.license ?? meta.licenses;
    if (typeof l === "string") return l;
    if (Array.isArray(l)) return l.map((x) => x.type ?? x).join(" OR ");
    if (l && typeof l === "object") return l.type ?? "";
    return "";
}

// "(MIT OR Apache-2.0)" passes when one side is allowed; "A AND B" needs both.
function allowed(expr) {
    const e = expr.replace(/[()]/g, " ").trim();
    if (e.includes(" OR ")) return e.split(" OR ").some((p) => allowed(p));
    if (e.includes(" AND ")) return e.split(" AND ").every((p) => allowed(p));
    return OSI.has(e.trim());
}

const bad = [];
for (const dir of dirs) {
    const meta = JSON.parse(readFileSync(path.join(dir, "package.json"), "utf8"));
    const lic = licence(meta);
    if (REVIEWED[meta.name] === lic) continue;
    if (!allowed(lic)) bad.push(`${meta.name}@${meta.version}: ${lic || "(none)"}`);
}
if (bad.length) {
    console.error(`licences not allowed (ADR-0007):\n  ${bad.join("\n  ")}`);
    process.exit(1);
}
console.log(`licences ok: ${dirs.length} production packages`);
