#!/usr/bin/env python3
"""Publish open slots in booking_v2 table."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish open slots")
    parser.add_argument("--date-from", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--date-to", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--opened-by", default="manual-script", help="opened_by marker")
    parser.add_argument(
        "--table-name",
        default="",
        help="Override DYNAMODB_TABLE_NAME for this execution",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview slots to be created without writing to DynamoDB",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.table_name.strip():
        os.environ["DYNAMODB_TABLE_NAME"] = args.table_name.strip()

    from app.config import settings
    from app.booking_v2.repository import booking_repository

    print(f"Using DynamoDB table: {settings.dynamodb_table_name}")

    if args.dry_run:
        preview = booking_repository.preview_slots_to_publish(
            date_from=args.date_from,
            date_to=args.date_to,
        )
        print("Preview de publicación de slots:")
        print(preview)
        return

    result = booking_repository.publish_slots(
        date_from=args.date_from,
        date_to=args.date_to,
        opened_by=args.opened_by,
    )
    print("Slots publication result:")
    print(result)


if __name__ == "__main__":
    main()
