"""Seed default categories. Run once after migrating: `python seed.py`."""

import asyncio

from sqlalchemy import select

from app.db.base import async_session_factory
from app.db.models import Category, Priority

DEFAULT_CATEGORIES: list[tuple[str, str, Priority]] = [
    ("Network", "Wifi, VPN, connectivity issues", Priority.HIGH),
    ("EHR / Clinical Systems", "Electronic health record and clinical application issues", Priority.HIGH),
    ("Hardware", "Workstations, monitors, peripherals", Priority.MEDIUM),
    ("Phones", "Desk phones, voicemail, softphone issues", Priority.MEDIUM),
    ("Account & Access", "Password resets, account lockouts, permissions", Priority.MEDIUM),
    ("Printers", "Printer/scanner hardware and driver issues", Priority.LOW),
    ("Other", "Anything that doesn't fit another category", Priority.MEDIUM),
]


async def seed() -> None:
    async with async_session_factory() as db:
        existing = (await db.execute(select(Category.name))).scalars().all()
        existing_names = set(existing)

        for name, description, default_priority in DEFAULT_CATEGORIES:
            if name in existing_names:
                continue
            db.add(Category(name=name, description=description, default_priority=default_priority))
            print(f"Seeding category: {name}")

        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
