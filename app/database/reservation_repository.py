"""Reservation repository with table allocation and slot availability."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from boto3.dynamodb.conditions import Attr, Key

from app.database.dynamodb_client import db_client

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"pending", "confirmed"}
VALID_STATUSES = {"pending", "confirmed", "cancelled"}
SLOT_MINUTES = 30
DEFAULT_RESERVATION_DURATION_MINUTES = 90

DEFAULT_TABLES: list[dict[str, Any]] = [
    {"table_id": "S1", "zone": "salon", "capacity_min": 1, "capacity_max": 2, "priority": 1, "is_active": True},
    {"table_id": "S2", "zone": "salon", "capacity_min": 1, "capacity_max": 2, "priority": 2, "is_active": True},
    {"table_id": "S3", "zone": "salon", "capacity_min": 2, "capacity_max": 4, "priority": 1, "is_active": True},
    {"table_id": "S4", "zone": "salon", "capacity_min": 2, "capacity_max": 4, "priority": 2, "is_active": True},
    {"table_id": "S5", "zone": "salon", "capacity_min": 4, "capacity_max": 6, "priority": 1, "is_active": True},
    {"table_id": "S6", "zone": "salon", "capacity_min": 6, "capacity_max": 8, "priority": 1, "is_active": True},
    {"table_id": "T1", "zone": "terraza", "capacity_min": 1, "capacity_max": 2, "priority": 1, "is_active": True},
    {"table_id": "T2", "zone": "terraza", "capacity_min": 1, "capacity_max": 2, "priority": 2, "is_active": True},
    {"table_id": "T3", "zone": "terraza", "capacity_min": 2, "capacity_max": 4, "priority": 1, "is_active": True},
    {"table_id": "T4", "zone": "terraza", "capacity_min": 2, "capacity_max": 4, "priority": 2, "is_active": True},
    {"table_id": "T5", "zone": "terraza", "capacity_min": 4, "capacity_max": 6, "priority": 1, "is_active": True},
]


class ReservationRepository:
    """Handles reservation persistence and availability rules."""

    def __init__(self) -> None:
        self.client = db_client
        self._seed_tables()

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def _seed_tables(self) -> None:
        try:
            for table in DEFAULT_TABLES:
                key = {"PK": f"TABLE#{table['table_id']}", "SK": "META"}
                existing = self.client.get_item(key)
                if existing:
                    continue

                self.client.put_item(
                    {
                        **key,
                        "entity_type": "table",
                        "table_id": table["table_id"],
                        "zone": table["zone"],
                        "capacity_min": table["capacity_min"],
                        "capacity_max": table["capacity_max"],
                        "priority": table["priority"],
                        "is_active": table["is_active"],
                        "created_at": self._now_iso(),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not seed table catalog: %s", str(exc))

    def _normalize_zone(self, preference: str) -> str | None:
        pref = (preference or "").lower()
        if "terraza" in pref:
            return "terraza"
        if "salon" in pref or "salón" in pref or "interior" in pref:
            return "salon"
        return None

    def _reservation_duration(self, num_people: int) -> int:
        _ = num_people
        return DEFAULT_RESERVATION_DURATION_MINUTES

    def _slot_keys(self, date: str, time: str, duration_minutes: int) -> list[str]:
        start = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        count = max(1, (duration_minutes + SLOT_MINUTES - 1) // SLOT_MINUTES)
        return [
            (start + timedelta(minutes=index * SLOT_MINUTES)).strftime("%Y-%m-%d#%H:%M")
            for index in range(count)
        ]

    def _validate_date_time(self, date: str, time: str) -> tuple[bool, str]:
        try:
            day = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return False, "Formato de fecha inválido. Usa YYYY-MM-DD"

        try:
            parsed_time = datetime.strptime(time, "%H:%M")
        except ValueError:
            return False, "Formato de hora inválido. Usa HH:MM"

        if parsed_time.minute not in (0, 30):
            return False, "Solo hay reservas cada 30 minutos (por ejemplo 20:00 o 20:30)"

        reservation_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        if reservation_dt < datetime.now(UTC):
            return False, "No se pueden crear reservas en fechas/horas pasadas"

        weekday = day.weekday()  # Monday = 0
        hour = parsed_time.hour
        minute = parsed_time.minute

        if weekday == 0:
            return False, "El restaurante está cerrado los lunes"

        if weekday in (1, 2, 3):
            lunch = (13 <= hour < 16) or (hour == 16 and minute == 0)
            dinner = (20 <= hour < 23) or (hour == 23 and minute <= 30)
            if lunch or dinner:
                return True, ""
            return False, "Martes a jueves: 13:00-16:00 y 20:00-23:30"

        if weekday in (4, 5):
            valid = (13 <= hour <= 23) or (hour == 0 and minute == 0)
            if valid:
                return True, ""
            return False, "Viernes y sábado: 13:00-00:00"

        if weekday == 6:
            valid = 13 <= hour < 17 or (hour == 17 and minute == 0)
            if valid:
                return True, ""
            return False, "Domingo: 13:00-17:00"

        return True, ""

    def _active_tables(self) -> list[dict[str, Any]]:
        tables = self.client.scan(
            FilterExpression=Attr("entity_type").eq("table") & Attr("is_active").eq(True)
        )
        ordered = sorted(
            tables,
            key=lambda table: (
                int(table.get("capacity_max", 99)),
                int(table.get("priority", 99)),
                str(table.get("table_id", "")),
            ),
        )
        return ordered

    def _slot_occupied(self, table_id: str, slot_key: str, reservation_id: str | None = None) -> bool:
        item = self.client.get_item({"PK": f"TABLE#{table_id}", "SK": f"SLOT#{slot_key}"})
        if not item:
            return False
        if reservation_id and item.get("reservation_id") == reservation_id:
            return False
        return item.get("status") in ACTIVE_STATUSES

    def _find_table_for_reservation(
        self,
        date: str,
        time: str,
        num_people: int,
        preferences: str,
        duration_minutes: int,
        reservation_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        slot_keys = self._slot_keys(date, time, duration_minutes)
        preferred_zone = self._normalize_zone(preferences)

        candidates = [
            table
            for table in self._active_tables()
            if int(table.get("capacity_min", 1)) <= num_people <= int(table.get("capacity_max", 999))
        ]

        if preferred_zone:
            preferred = [table for table in candidates if table.get("zone") == preferred_zone]
            if preferred:
                candidates = preferred

        for table in candidates:
            if all(
                not self._slot_occupied(table["table_id"], slot_key, reservation_id=reservation_id)
                for slot_key in slot_keys
            ):
                return table, slot_keys

        return None, slot_keys

    def _reservation_key(self, reservation_id: str) -> dict[str, str]:
        return {"PK": f"RESERVATION#{reservation_id}", "SK": "DETAILS"}

    def _customer_key(self, phone: str, date: str, time: str, reservation_id: str) -> dict[str, str]:
        return {
            "PK": f"CUSTOMER#{phone}",
            "SK": f"RESERVATION#{date}#{time}#{reservation_id}",
        }

    def _save_customer_lookup(self, reservation: dict[str, Any]) -> None:
        key = self._customer_key(
            reservation["phone"], reservation["date"], reservation["time"], reservation["id"]
        )
        self.client.put_item(
            {
                **key,
                "entity_type": "customer_lookup",
                "reservation_id": reservation["id"],
                "status": reservation["status"],
                "date": reservation["date"],
                "time": reservation["time"],
                "updated_at": self._now_iso(),
            }
        )

    def _delete_customer_lookup(self, reservation: dict[str, Any]) -> None:
        self.client.delete_item(
            self._customer_key(reservation["phone"], reservation["date"], reservation["time"], reservation["id"])
        )

    def _save_occupancy(
        self,
        table_id: str,
        slot_keys: list[str],
        reservation: dict[str, Any],
    ) -> tuple[bool, str]:
        created_keys: list[dict[str, str]] = []

        for slot_key in slot_keys:
            key = {"PK": f"TABLE#{table_id}", "SK": f"SLOT#{slot_key}"}
            ttl_epoch = int((datetime.now(UTC) + timedelta(days=3)).timestamp())
            ok = self.client.put_item(
                {
                    **key,
                    "entity_type": "occupancy",
                    "table_id": table_id,
                    "reservation_id": reservation["id"],
                    "status": reservation["status"],
                    "date": reservation["date"],
                    "time": slot_key.split("#", 1)[1],
                    "ttl": ttl_epoch,
                    "updated_at": self._now_iso(),
                },
                condition_expression="attribute_not_exists(PK)",
            )

            if not ok:
                for created in created_keys:
                    self.client.delete_item(created)
                return False, f"El slot {slot_key} ya no estaba disponible"

            created_keys.append(key)

        return True, ""

    def _release_occupancy(self, table_id: str, slot_keys: list[str], reservation_id: str) -> None:
        for slot_key in slot_keys:
            self.client.delete_item(
                {"PK": f"TABLE#{table_id}", "SK": f"SLOT#{slot_key}"},
                condition_expression="reservation_id = :rid",
                expression_attribute_values={":rid": reservation_id},
            )

    def _build_reservation_item(self, reservation: dict[str, Any]) -> dict[str, Any]:
        return {
            **self._reservation_key(reservation["id"]),
            "entity_type": "reservation",
            "GSI1PK": f"DATE#{reservation['date']}",
            "GSI1SK": f"TIME#{reservation['time']}#RES#{reservation['id']}",
            "id": reservation["id"],
            "date": reservation["date"],
            "time": reservation["time"],
            "status": reservation["status"],
            "num_people": reservation["num_people"],
            "customer_name": reservation["customer_name"],
            "phone": reservation["phone"],
            "preferences": reservation.get("preferences", ""),
            "special_occasion": reservation.get("special_occasion", ""),
            "table_id": reservation.get("table_id", ""),
            "table_zone": reservation.get("table_zone", ""),
            "duration_min": reservation["duration_min"],
            "created_at": reservation["created_at"],
            "updated_at": reservation["updated_at"],
        }

    def get_reservation(self, reservation_id: str) -> dict[str, Any] | None:
        return self.client.get_item(self._reservation_key(reservation_id))

    def create_reservation(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        valid, message = self._validate_date_time(payload["date"], payload["time"])
        if not valid:
            return False, message, None

        if payload["num_people"] < 1 or payload["num_people"] > 12:
            return False, "El número de personas debe estar entre 1 y 12", None

        reservation_id = payload.get("id") or f"RES-{payload['date'].replace('-', '')}-{uuid4().hex[:6].upper()}"
        duration = payload.get("duration_min") or self._reservation_duration(payload["num_people"])
        now = self._now_iso()

        reservation = {
            "id": reservation_id,
            "date": payload["date"],
            "time": payload["time"],
            "num_people": int(payload["num_people"]),
            "customer_name": payload["customer_name"],
            "phone": payload["phone"],
            "preferences": payload.get("preferences", ""),
            "special_occasion": payload.get("special_occasion", ""),
            "status": payload.get("status", "pending"),
            "duration_min": int(duration),
            "created_at": now,
            "updated_at": now,
        }

        table, slot_keys = self._find_table_for_reservation(
            reservation["date"],
            reservation["time"],
            reservation["num_people"],
            reservation["preferences"],
            reservation["duration_min"],
            reservation_id=reservation_id,
        )

        if not table:
            return False, "No hay mesas disponibles para ese horario", None

        reservation["table_id"] = table["table_id"]
        reservation["table_zone"] = table.get("zone", "salon")

        saved = self.client.put_item(
            self._build_reservation_item(reservation),
            condition_expression="attribute_not_exists(PK)",
        )
        if not saved:
            return False, "No se pudo guardar la reserva (puede que el ID ya exista)", None

        self._save_customer_lookup(reservation)

        if reservation["status"] in ACTIVE_STATUSES:
            occupancy_saved, occupancy_error = self._save_occupancy(table["table_id"], slot_keys, reservation)
            if not occupancy_saved:
                self.client.delete_item(self._reservation_key(reservation["id"]))
                self._delete_customer_lookup(reservation)
                return False, occupancy_error, None

        return True, "", reservation

    def update_reservation(self, reservation_id: str, updates: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        current = self.get_reservation(reservation_id)
        if not current:
            return False, "No existe la reserva indicada", None

        merged = {**current, **updates}
        merged["id"] = reservation_id
        merged["status"] = merged.get("status", "pending").lower()
        merged["updated_at"] = self._now_iso()

        if merged["status"] not in VALID_STATUSES:
            return False, "Estado inválido. Usa pending, confirmed o cancelled", None

        if int(merged["num_people"]) < 1 or int(merged["num_people"]) > 12:
            return False, "El número de personas debe estar entre 1 y 12", None

        if not merged.get("duration_min"):
            merged["duration_min"] = self._reservation_duration(int(merged["num_people"]))

        current_active = current.get("status") in ACTIVE_STATUSES
        target_active = merged.get("status") in ACTIVE_STATUSES

        date_or_time_changed = (
            current.get("date") != merged.get("date") or current.get("time") != merged.get("time")
        )
        if target_active or date_or_time_changed:
            valid, message = self._validate_date_time(merged["date"], merged["time"])
            if not valid:
                return False, message, None

        reallocate = any(
            current.get(field) != merged.get(field)
            for field in ("date", "time", "num_people", "preferences", "status")
        )

        new_slot_keys: list[str] = []
        if target_active and reallocate:
            table, new_slot_keys = self._find_table_for_reservation(
                merged["date"],
                merged["time"],
                int(merged["num_people"]),
                merged.get("preferences", ""),
                int(merged["duration_min"]),
                reservation_id=reservation_id,
            )
            if not table:
                return False, "No hay disponibilidad para los cambios solicitados", None
            merged["table_id"] = table["table_id"]
            merged["table_zone"] = table.get("zone", "salon")

        if target_active and reallocate:
            ok, error = self._save_occupancy(merged["table_id"], new_slot_keys, merged)
            if not ok:
                return False, error, None

        if current_active and current.get("table_id"):
            old_slots = self._slot_keys(
                current["date"],
                current["time"],
                int(current.get("duration_min", DEFAULT_RESERVATION_DURATION_MINUTES)),
            )
            if target_active:
                keep = {f"SLOT#{slot}" for slot in new_slot_keys if current.get("table_id") == merged.get("table_id")}
                release = [slot for slot in old_slots if f"SLOT#{slot}" not in keep]
            else:
                release = old_slots
            self._release_occupancy(current["table_id"], release, reservation_id)

        self.client.put_item(self._build_reservation_item(merged))

        old_lookup = self._customer_key(current["phone"], current["date"], current["time"], reservation_id)
        new_lookup = self._customer_key(merged["phone"], merged["date"], merged["time"], reservation_id)
        if old_lookup != new_lookup:
            self.client.delete_item(old_lookup)
        self._save_customer_lookup(merged)

        return True, "", merged

    def cancel_reservation(self, reservation_id: str) -> tuple[bool, str, dict[str, Any] | None]:
        return self.update_reservation(reservation_id, {"status": "cancelled"})

    def query_reservations_by_date(self, date: str) -> list[dict[str, Any]]:
        return self.client.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"DATE#{date}"),
        )

    def query_reservations_by_status(self, status: str, date: str | None = None) -> list[dict[str, Any]]:
        status_value = status.lower()
        if date:
            return self.client.query(
                IndexName="StatusDateIndex",
                KeyConditionExpression=Key("status").eq(status_value) & Key("date").eq(date),
            )
        return self.client.query(
            IndexName="StatusDateIndex",
            KeyConditionExpression=Key("status").eq(status_value),
        )

    def scan_all_reservations(self, status_filter: str | None = None) -> list[dict[str, Any]]:
        if status_filter:
            return self.client.scan(
                FilterExpression=Attr("entity_type").eq("reservation") & Attr("status").eq(status_filter)
            )
        return self.client.scan(FilterExpression=Attr("entity_type").eq("reservation"))

    def list_reservations(
        self,
        *,
        date: str = "",
        status: str = "all",
        customer_name: str = "",
        phone: str = "",
    ) -> list[dict[str, Any]]:
        status = status.lower()

        if date:
            items = self.query_reservations_by_date(date)
        elif status in VALID_STATUSES:
            items = self.query_reservations_by_status(status)
        else:
            items = self.scan_all_reservations()

        reservations = [item for item in items if item.get("entity_type") == "reservation"]

        if status in VALID_STATUSES:
            reservations = [row for row in reservations if row.get("status") == status]

        if customer_name:
            name_query = customer_name.lower().strip()
            reservations = [
                row for row in reservations if name_query in row.get("customer_name", "").lower()
            ]

        if phone:
            phone_query = phone.strip()
            reservations = [row for row in reservations if row.get("phone") == phone_query]

        reservations.sort(key=lambda row: (row.get("date", ""), row.get("time", ""), row.get("id", "")))
        return reservations

    def available_times(self, date: str, num_people: int, preferred_zone: str = "") -> list[dict[str, Any]]:
        times = [
            "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00",
            "20:00", "20:30", "21:00", "21:30", "22:00", "22:30", "23:00", "23:30",
        ]

        available: list[dict[str, Any]] = []
        for time in times:
            valid, _ = self._validate_date_time(date, time)
            if not valid:
                continue

            table, _ = self._find_table_for_reservation(
                date,
                time,
                num_people,
                preferred_zone,
                self._reservation_duration(num_people),
            )
            if table:
                available.append(
                    {
                        "time": time,
                        "table_id": table["table_id"],
                        "zone": table.get("zone", "salon"),
                    }
                )

        return available


reservation_repository = ReservationRepository()
