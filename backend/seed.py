"""Seed default categories. Run once after migrating: `python seed.py`."""

import asyncio

from sqlalchemy import select

from app.db.base import async_session_factory
from app.db.models import Category, Priority

DEFAULT_CATEGORIES: list[tuple[str, str, Priority]] = [
    ("Network", "Wifi, VPN, connectivity issues", Priority.HIGH),
    ("eClinicalWorks", "eClinicalWorks EHR access, performance, and module issues", Priority.HIGH),
    ("Microsoft 365", "Outlook, Teams, Word/Excel, OneDrive, SharePoint", Priority.MEDIUM),
    ("Password", "Password resets, account lockouts, MFA", Priority.MEDIUM),
    ("Printer", "Printer/scanner hardware and driver issues", Priority.LOW),
    ("EHR / Clinical Systems", "Electronic health record and clinical application issues", Priority.HIGH),
    ("Hardware", "Workstations, monitors, peripherals", Priority.MEDIUM),
    ("Phones", "Desk phones, voicemail, softphone issues", Priority.MEDIUM),
    ("Account & Access", "Password resets, account lockouts, permissions", Priority.MEDIUM),
    ("Other", "Anything that doesn't fit another category", Priority.MEDIUM),
]

# Phase 2 renamed the printer category to the singular form the voice agent
# classifies into. Applied before inserts so the rename doesn't collide.
RENAMES: list[tuple[str, str]] = [("Printers", "Printer")]


async def seed() -> None:
    async with async_session_factory() as db:
        for old_name, new_name in RENAMES:
            existing_names = set((await db.execute(select(Category.name))).scalars().all())
            if old_name in existing_names and new_name not in existing_names:
                category = (
                    await db.execute(select(Category).where(Category.name == old_name))
                ).scalar_one()
                category.name = new_name
                print(f"Renaming category: {old_name} -> {new_name}")
        await db.commit()

        existing_names = set((await db.execute(select(Category.name))).scalars().all())

        for name, description, default_priority in DEFAULT_CATEGORIES:
            if name in existing_names:
                continue
            db.add(Category(name=name, description=description, default_priority=default_priority))
            print(f"Seeding category: {name}")

        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
