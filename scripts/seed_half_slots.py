#!/usr/bin/env python3
"""Seed DynamoDB reservations until reaching a target slot occupancy ratio.

Usage:
  python3 scripts/seed_half_slots.py
  python3 scripts/seed_half_slots.py --days 7 --target-ratio 0.5 --dry-run
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr

# Ensure repository root is importable when running as a script (e.g. `uv run scripts/...`).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ACTIVE_STATUSES: set[str] = set()
reservation_repository = None

FIRST_NAMES = [
    "Ana",
    "Luis",
    "Marta",
    "Carlos",
    "Sofía",
    "Javier",
    "Lucía",
    "Diego",
    "Elena",
    "Pablo",
    "Raquel",
    "Hugo",
]

LAST_NAMES = [
    "García",
    "Pérez",
    "Martín",
    "López",
    "Sánchez",
    "Torres",
    "Vega",
    "Ruiz",
    "Navarro",
    "Romero",
]

PREFERENCES = [
    "",
    "",
    "terraza",
    "salon",
    "cumpleaños",
    "mesa tranquila",
    "cerca de ventana",
]

SPECIAL_OCCASIONS = ["", "", "cumpleaños", "aniversario", "cena de empresa"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rellena slots de reservas en DynamoDB")
    parser.add_argument("--days", type=int, default=7, help="Número de días desde hoy (default: 7)")
    parser.add_argument(
        "--target-ratio",
        type=float,
        default=0.50,
        help="Porcentaje objetivo de ocupación de slots (0.0-1.0). Default: 0.50",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3000,
        help="Intentos máximos de creación de reservas. Default: 3000",
    )
    parser.add_argument("--seed", type=int, default=42, help="Semilla random para reproducibilidad")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No escribe en Dynamo, solo muestra cálculo de objetivo",
    )
    return parser.parse_args()


def validate_aws_credentials() -> None:
    from app.config import settings  # noqa: WPS433

    sts_kwargs: dict[str, str] = {
        "region_name": settings.aws_region,
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
    }
    if settings.aws_session_token:
        sts_kwargs["aws_session_token"] = settings.aws_session_token

    sts = boto3.client("sts", **sts_kwargs)
    try:
        identity = sts.get_caller_identity()
        account = identity.get("Account", "unknown")
        arn = identity.get("Arn", "unknown")
        print(f"AWS identity OK: account={account}, arn={arn}")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        message = exc.response.get("Error", {}).get("Message", str(exc))
        raise SystemExit(
            "Credenciales AWS inválidas o expiradas.\n"
            f"STS error: {code} - {message}\n"
            "Revisa AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN en tu .env."
        ) from exc


@dataclass(frozen=True)
class DayWindow:
    date: str
    start_times: tuple[str, ...]


def half_hour_times(start_hhmm: str, end_hhmm: str) -> list[str]:
    start = datetime.strptime(start_hhmm, "%H:%M")
    end = datetime.strptime(end_hhmm, "%H:%M")

    out: list[str] = []
    current = start
    while current <= end:
        out.append(current.strftime("%H:%M"))
        current += timedelta(minutes=30)
    return out


def valid_times_for_weekday(weekday: int) -> list[str]:
    # Monday=0 ... Sunday=6
    if weekday == 0:
        return []

    if weekday in (1, 2, 3):
        return half_hour_times("13:00", "16:00") + half_hour_times("20:00", "23:30")

    if weekday in (4, 5):
        return half_hour_times("13:00", "23:30") + ["00:00"]

    return half_hour_times("13:00", "17:00")


def build_date_windows(days: int) -> list[DayWindow]:
    now = datetime.now(UTC)
    windows: list[DayWindow] = []

    for offset in range(days):
        day = (now + timedelta(days=offset)).date()
        date_str = day.strftime("%Y-%m-%d")
        weekday = day.weekday()
        times = valid_times_for_weekday(weekday)

        future_only: list[str] = []
        for time_str in times:
            dt_value = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
            if dt_value > now + timedelta(minutes=15):
                future_only.append(time_str)

        if future_only:
            windows.append(DayWindow(date=date_str, start_times=tuple(future_only)))

    return windows


def count_active_tables() -> int:
    tables = reservation_repository.client.scan(
        FilterExpression=Attr("entity_type").eq("table") & Attr("is_active").eq(True)
    )
    return len(tables)


def estimate_total_slot_capacity(windows: Iterable[DayWindow], active_tables: int) -> int:
    return sum(len(window.start_times) * active_tables for window in windows)


def count_used_slots_in_range(start_date: str, end_date: str) -> int:
    occupancy = reservation_repository.client.scan(FilterExpression=Attr("entity_type").eq("occupancy"))
    return sum(
        1
        for row in occupancy
        if start_date <= str(row.get("date", "")) <= end_date and str(row.get("status", "")) in ACTIVE_STATUSES
    )


def random_customer() -> tuple[str, str]:
    full_name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    phone = f"34{random.randint(600000000, 799999999)}"
    return full_name, phone


def create_random_reservation(windows: list[DayWindow]) -> bool:
    day = random.choice(windows)
    time_value = random.choice(day.start_times)
    name, phone = random_customer()
    num_people = random.choices([2, 3, 4, 5, 6, 7, 8], weights=[25, 20, 18, 14, 10, 8, 5], k=1)[0]
    preferences = random.choice(PREFERENCES)
    special_occasion = random.choice(SPECIAL_OCCASIONS)

    success, _, reservation = reservation_repository.create_reservation(
        {
            "date": day.date,
            "time": time_value,
            "num_people": num_people,
            "customer_name": name,
            "phone": phone,
            "preferences": preferences,
            "special_occasion": special_occasion,
            "status": "pending",
        }
    )

    if not success or not reservation:
        return False

    # Mezcla de estados realista: ~65% confirmadas
    if random.random() < 0.65:
        reservation_repository.update_reservation(reservation["id"], {"status": "confirmed"})

    return True


def main() -> None:
    global ACTIVE_STATUSES, reservation_repository

    args = parse_args()
    validate_aws_credentials()

    from app.database.reservation_repository import (  # noqa: WPS433
        ACTIVE_STATUSES as REPO_ACTIVE_STATUSES,
        reservation_repository as REPO_INSTANCE,
    )

    ACTIVE_STATUSES = REPO_ACTIVE_STATUSES
    reservation_repository = REPO_INSTANCE

    if args.days < 1:
        raise SystemExit("--days debe ser >= 1")

    if not (0.0 < args.target_ratio <= 1.0):
        raise SystemExit("--target-ratio debe estar entre 0.0 y 1.0")

    random.seed(args.seed)

    windows = build_date_windows(args.days)
    if not windows:
        raise SystemExit("No hay ventanas válidas en el rango indicado")

    active_tables = count_active_tables()
    if active_tables == 0:
        raise SystemExit("No hay mesas activas. Inicializa el catálogo de mesas antes de ejecutar este script.")

    start_date = windows[0].date
    end_date = windows[-1].date

    total_capacity = estimate_total_slot_capacity(windows, active_tables)
    target_used_slots = int(total_capacity * args.target_ratio)
    current_used_slots = count_used_slots_in_range(start_date, end_date)

    print("=== Seed de reservas (slots) ===")
    print(f"Rango: {start_date} -> {end_date}")
    print(f"Mesas activas: {active_tables}")
    print(f"Capacidad total (slots mesa+tiempo): {total_capacity}")
    print(f"Slots ocupados actuales: {current_used_slots}")
    print(f"Objetivo ({args.target_ratio:.0%}): {target_used_slots} slots")

    if args.dry_run:
        print("Dry-run activado. No se han creado reservas.")
        return

    if current_used_slots >= target_used_slots:
        print("El objetivo ya está cumplido. No se crean nuevas reservas.")
        return

    created = 0
    attempts = 0

    while attempts < args.max_attempts:
        attempts += 1
        ok = create_random_reservation(windows)
        if ok:
            created += 1

        if attempts % 20 == 0:
            current_used_slots = count_used_slots_in_range(start_date, end_date)
            if current_used_slots >= target_used_slots:
                break

    final_used_slots = count_used_slots_in_range(start_date, end_date)
    final_ratio = (final_used_slots / total_capacity) if total_capacity else 0.0

    print("\n=== Resultado ===")
    print(f"Intentos: {attempts}")
    print(f"Reservas creadas: {created}")
    print(f"Slots ocupados finales: {final_used_slots}/{total_capacity}")
    print(f"Ocupación final: {final_ratio:.2%}")


if __name__ == "__main__":
    main()
