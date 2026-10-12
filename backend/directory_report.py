"""Validate the employee directory CSV with the same code the API uses.

    python directory_report.py [path/to/users.csv]

Defaults to USER_DIRECTORY_CSV_PATH. Prints counts and line numbers only: no names, numbers or
addresses. Changes nothing, including the file.
"""

import asyncio
import sys

from app.core.config import get_settings
from app.services.user_directory import UserDirectoryService


async def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else get_settings().user_directory_csv_path
    if not path:
        print("No path given and USER_DIRECTORY_CSV_PATH is empty.")
        return 2
    service = UserDirectoryService()
    await service.load(path)
    if not service.available:
        print(f"UNAVAILABLE: {service.unavailable_reason}")
        return 1
    report = service.report
    for key, value in report.summary().items():
        print(f"{key:30} {value}")
    if report.rejected_rows:
        print("\nrejected lines (line: reason):")
        for line, reason in report.rejected_rows:
            print(f"  {line}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
