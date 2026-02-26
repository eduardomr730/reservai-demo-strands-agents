#!/usr/bin/env python3
"""Seed bookings in booking v2 until reaching a target slot occupancy ratio.

Usage:
  python3 scripts/seed_half_slots.py
  python3 scripts/seed_half_slots.py --days 7 --target-ratio 0.5 --dry-run
  python3 scripts/seed_half_slots.py --days 14 --auto-publish
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

# Ensure repository root is importable when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

booking_repository = None

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


@dataclass(frozen=True)
class DayWindow:
    date: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rellena slots abiertos en DynamoDB con reservas")
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
        "--auto-publish",
        action="store_true",
        help="Publica slots para el rango antes de sembrar reservas",
    )
    parser.add_argument(
        "--opened-by",
        type=str,
        default="seed-script",
        help="Identificador para opened_by al publicar slots",
    )
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


def build_date_windows(days: int) -> list[DayWindow]:
    now = datetime.now(UTC)
    windows: list[DayWindow] = []

    for offset in range(days):
        day = (now + timedelta(days=offset)).date()
        windows.append(DayWindow(date=day.strftime("%Y-%m-%d")))

    return windows


def slot_counts(windows: Iterable[DayWindow]) -> tuple[int, int]:
    total = 0
    booked = 0

    for window in windows:
        stats = booking_repository.slot_stats(window.date)
        total += int(stats.get("total", 0))
        booked += int(stats.get("booked", 0))

    return total, booked


def random_customer() -> tuple[str, str]:
    full_name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    phone = f"34{random.randint(600000000, 799999999)}"
    return full_name, phone


def create_random_reservation(windows: list[DayWindow]) -> bool:
    day = random.choice(windows)
    people = random.choices([2, 3, 4, 5, 6, 7, 8], weights=[25, 20, 18, 14, 10, 8, 5], k=1)[0]
    preferences = random.choice(PREFERENCES)

    available = booking_repository.available_times(day.date, people, preferences)
    if not available:
        return False

    slot = random.choice(available)
    name, phone = random_customer()
    special_occasion = random.choice(SPECIAL_OCCASIONS)

    success, _, reservation = booking_repository.create_reservation(
        {
            "date": day.date,
            "time": slot["time"],
            "num_people": people,
            "customer_name": name,
            "phone": phone,
            "preferences": preferences,
            "special_occasion": special_occasion,
            "status": "pending",
        }
    )

    if not success or not reservation:
        return False

    if random.random() < 0.65:
        booking_repository.update_reservation(reservation["id"], {"status": "confirmed"})

    return True


def main() -> None:
    global booking_repository

    args = parse_args()
    validate_aws_credentials()

    from app.booking_v2.repository import booking_repository as BOOKING_REPOSITORY  # noqa: WPS433

    booking_repository = BOOKING_REPOSITORY

    if args.days < 1:
        raise SystemExit("--days debe ser >= 1")

    if not (0.0 < args.target_ratio <= 1.0):
        raise SystemExit("--target-ratio debe estar entre 0.0 y 1.0")

    random.seed(args.seed)

    windows = build_date_windows(args.days)
    if not windows:
        raise SystemExit("No hay ventanas válidas en el rango indicado")

    start_date = windows[0].date
    end_date = windows[-1].date

    if args.auto_publish and not args.dry_run:
        publish_result = booking_repository.publish_slots(
            date_from=start_date,
            date_to=end_date,
            opened_by=args.opened_by,
        )
        print(f"Slots publicados: {publish_result}")

    total_slots, booked_slots = slot_counts(windows)
    target_booked_slots = int(total_slots * args.target_ratio)

    print("=== Seed de reservas (booking v2) ===")
    print(f"Rango: {start_date} -> {end_date}")
    print(f"Slots totales: {total_slots}")
    print(f"Slots booked actuales: {booked_slots}")
    print(f"Objetivo ({args.target_ratio:.0%}): {target_booked_slots} slots booked")

    if total_slots == 0:
        raise SystemExit(
            "No hay slots abiertos en el rango. Publica slots primero con /admin/publish-slots "
            "o ejecuta con --auto-publish."
        )

    if args.dry_run:
        print("Dry-run activado. No se han creado reservas.")
        return

    if booked_slots >= target_booked_slots:
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
            _, booked_slots = slot_counts(windows)
            if booked_slots >= target_booked_slots:
                break

    total_slots, final_booked_slots = slot_counts(windows)
    final_ratio = (final_booked_slots / total_slots) if total_slots else 0.0

    print("\n=== Resultado ===")
    print(f"Intentos: {attempts}")
    print(f"Reservas creadas: {created}")
    print(f"Slots booked finales: {final_booked_slots}/{total_slots}")
    print(f"Ocupación final: {final_ratio:.2%}")


if __name__ == "__main__":
    main()
