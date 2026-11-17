"""Seed a demo brain: an identity core and a few memories for one employee.

    uv run python tools/seed_demo.py [employee_id] [tenant_id]

Writes straight to brain_db, for local demos only. Real memories arrive through the governed
write path (Remember, Phase 5); real identity changes through approved proposals (Phase 8).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from wfk_platform.db import Database
from wfk_platform.migrate import Runner, ensure_database

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.postgres import brain as brain_repo
from brain_service.adapters.postgres import memories as repo
from brain_service.domain.memory.identity import Identity
from brain_service.domain.memory.model import Memory
from brain_service.settings import Settings

DEMO_TENANT = "0193a7c4-0000-7000-8000-00000000d3e0"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

IDENTITY = Identity(
    role="Summarises documents for the Acme operations team.",
    values=["Accuracy over speed", "Plain words"],
    style="Short sentences. Lead with the point.",
    hard_constraints=["Never invent numbers that are not in the text"],
    escalation=["The text contains personal data about a customer"],
)

MEMORIES = [
    ("semantic", "human_verified", "The ops team wants summaries of at most two sentences."),
    ("semantic", "system", "Acme reports use fiscal quarters starting in February."),
    ("episodic", "system", "Summarised the Q3 incident report; the manager asked for less jargon."),
]


async def main(employee_id: str, tenant_id: str) -> None:
    settings = Settings()
    ensure_database(settings.database_url)
    Runner(settings.database_url, MIGRATIONS).up()
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
    await db.open()
    version = await brain_repo.activate_identity(db, tenant_id, employee_id, IDENTITY, by="seed")
    embed = HashEmbedder()
    async with db.tenant_tx(tenant_id) as conn:
        for kind, trust, content in MEMORIES:
            memory = Memory.model_validate(
                {"tenant_id": tenant_id, "employee_id": employee_id, "kind": kind,
                 "trust": trust, "content": content, "source_ref": "seed_demo"}
            )  # fmt: skip
            await repo.insert(conn, memory, embed.embed_one(content))
    await db.close()
    print(f"seeded {employee_id}: identity v{version}, {len(MEMORIES)} memories")


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    args = sys.argv[1:]
    asyncio.run(
        main(args[0] if args else "summariser", args[1] if len(args) > 1 else DEMO_TENANT),
        loop_factory=factory,
    )
