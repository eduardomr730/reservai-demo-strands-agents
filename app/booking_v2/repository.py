"""Booking v2 repository with explicit slot publishing and reservation lifecycle."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from boto3.dynamodb.conditions import Attr, Key

from app.database.dynamodb_client import db_client

logger = logging.getLogger(__name__)

VALID_STATUSES = {"pending", "confirmed", "cancelled"}
ACTIVE_STATUSES = {"pending", "confirmed"}

DEFAULT_SLOT_STATUS = "open"
HOLD_TTL_SECONDS = 5 * 60

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


class BookingV2Repository:
    """Handles slots and reservations as separate but transactional entities."""

    def __init__(self) -> None:
        self.client = db_client
        self._seed_table_catalog()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def _normalize_zone(self, preference: str) -> str | None:
        pref = (preference or "").lower()
        if "terraza" in pref:
            return "terraza"
        if "salon" in pref or "salón" in pref or "interior" in pref:
            return "salon"
        return None

    def _reservation_id(self, date: str) -> str:
        return f"RES-{date.replace('-', '')}-{uuid4().hex[:8].upper()}"

    def _slot_pk(self, date: str) -> str:
        return f"DAY#{date}"

    def _slot_sk(self, time: str, table_id: str) -> str:
        return f"SLOT#{time}#TABLE#{table_id}"

    def _slot_key(self, date: str, time: str, table_id: str) -> dict[str, str]:
        return {"PK": self._slot_pk(date), "SK": self._slot_sk(time, table_id)}

    def _slot_ref(self, date: str, time: str, table_id: str) -> str:
        return f"{date}#{time}#{table_id}"

    def _reservation_key(self, reservation_id: str) -> dict[str, str]:
        return {"PK": f"RES#{reservation_id}", "SK": "META"}

    def _customer_lookup_key(self, phone: str, date: str, time: str, reservation_id: str) -> dict[str, str]:
        return {
            "PK": f"PHONE#{phone}",
            "SK": f"RES#{date}#{time}#{reservation_id}",
        }

    def _reservation_ttl(self, date: str, keep_days: int = 180) -> int:
        parsed = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        return int((parsed + timedelta(days=keep_days)).timestamp())

    def _held_ttl(self) -> int:
        return int((self._now() + timedelta(seconds=HOLD_TTL_SECONDS)).timestamp())

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
        if reservation_dt < self._now():
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

    def _times_for_weekday(self, weekday: int) -> list[str]:
        if weekday == 0:
            return []
        if weekday in (1, 2, 3):
            return [
                "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00",
                "20:00", "20:30", "21:00", "21:30", "22:00", "22:30", "23:00", "23:30",
            ]
        if weekday in (4, 5):
            return [
                "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30",
                "17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30",
                "21:00", "21:30", "22:00", "22:30", "23:00", "23:30", "00:00",
            ]
        return ["13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]

    def _seed_table_catalog(self) -> None:
        for table in DEFAULT_TABLES:
            key = {"PK": f"TABLE#{table['table_id']}", "SK": "META"}
            existing = self.client.get_item(key)
            if existing:
                continue
            self.client.put_item(
                {
                    **key,
                    "entity_type": "table_meta",
                    "table_id": table["table_id"],
                    "zone": table["zone"],
                    "capacity_min": table["capacity_min"],
                    "capacity_max": table["capacity_max"],
                    "priority": table["priority"],
                    "is_active": table["is_active"],
                    "created_at": self._now_iso(),
                }
            )

    def list_active_tables(self) -> list[dict[str, Any]]:
        tables = self.client.scan(
            FilterExpression=(
                (Attr("entity_type").eq("table_meta") | Attr("entity_type").eq("table"))
                & Attr("is_active").eq(True)
            )
        )
        return sorted(
            tables,
            key=lambda table: (
                int(table.get("capacity_max", 99)),
                int(table.get("priority", 99)),
                str(table.get("table_id", "")),
            ),
        )

    def publish_slots(self, date_from: str, date_to: str, *, opened_by: str = "system") -> dict[str, int]:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("date_to debe ser igual o posterior a date_from")

        tables = self.list_active_tables()
        if not tables:
            raise ValueError("No hay mesas activas configuradas")

        created = 0
        skipped_existing = 0

        cursor = start
        while cursor <= end:
            date_value = cursor.strftime("%Y-%m-%d")
            times = self._times_for_weekday(cursor.weekday())
            for time_value in times:
                valid, _ = self._validate_date_time(date_value, time_value)
                if not valid:
                    continue

                for table in tables:
                    item = {
                        "PK": self._slot_pk(date_value),
                        "SK": self._slot_sk(time_value, table["table_id"]),
                        "entity_type": "slot",
                        "slot_ref": self._slot_ref(date_value, time_value, table["table_id"]),
                        "date": date_value,
                        "time": time_value,
                        "table_id": table["table_id"],
                        "zone": table.get("zone", "salon"),
                        "capacity_min": int(table.get("capacity_min", 1)),
                        "capacity_max": int(table.get("capacity_max", 99)),
                        "priority": int(table.get("priority", 99)),
                        "status": DEFAULT_SLOT_STATUS,
                        "reservation_id": "",
                        "hold_expires_at": 0,
                        "version": 1,
                        "GSI1PK": f"DAY#{date_value}#STATUS#{DEFAULT_SLOT_STATUS}",
                        "GSI1SK": f"TIME#{time_value}#TABLE#{table['table_id']}",
                        "created_at": self._now_iso(),
                        "updated_at": self._now_iso(),
                        "opened_by": opened_by,
                        "opened_at": self._now_iso(),
                    }
                    saved = self.client.put_item(item, condition_expression="attribute_not_exists(PK)")
                    if saved:
                        created += 1
                    else:
                        skipped_existing += 1

            cursor += timedelta(days=1)

        return {"created": created, "skipped_existing": skipped_existing}

    def preview_slots_to_publish(self, date_from: str, date_to: str) -> dict[str, Any]:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("date_to debe ser igual o posterior a date_from")

        tables = self.list_active_tables()
        if not tables:
            return {"table_count": 0, "total_slots": 0, "days": []}

        per_day: list[dict[str, Any]] = []
        total_slots = 0
        cursor = start
        while cursor <= end:
            date_value = cursor.strftime("%Y-%m-%d")
            times = self._times_for_weekday(cursor.weekday())
            valid_times: list[str] = []
            for time_value in times:
                valid, _ = self._validate_date_time(date_value, time_value)
                if valid:
                    valid_times.append(time_value)
            day_slots = len(valid_times) * len(tables)
            total_slots += day_slots
            per_day.append(
                {
                    "date": date_value,
                    "weekday": cursor.strftime("%A"),
                    "times": valid_times,
                    "time_count": len(valid_times),
                    "slot_count": day_slots,
                }
            )
            cursor += timedelta(days=1)

        return {
            "table_count": len(tables),
            "table_ids": [str(table.get("table_id")) for table in tables],
            "total_slots": total_slots,
            "days": per_day,
        }

    def _query_open_slots(self, date: str) -> list[dict[str, Any]]:
        return self.client.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"DAY#{date}#STATUS#open"),
        )

    def available_times(self, date: str, num_people: int, preferred_zone: str = "") -> list[dict[str, Any]]:
        if num_people < 1 or num_people > 12:
            return []

        zone = self._normalize_zone(preferred_zone)
        open_slots = self._query_open_slots(date)

        filtered = [
            slot
            for slot in open_slots
            if int(slot.get("capacity_min", 1)) <= num_people <= int(slot.get("capacity_max", 99))
            and (zone is None or slot.get("zone") == zone)
        ]

        filtered.sort(
            key=lambda slot: (
                slot.get("time", ""),
                int(slot.get("priority", 99)),
                slot.get("table_id", ""),
            )
        )

        by_time: dict[str, dict[str, Any]] = {}
        for slot in filtered:
            time_value = str(slot.get("time", ""))
            if time_value in by_time:
                continue
            by_time[time_value] = {
                "time": time_value,
                "table_id": slot.get("table_id", ""),
                "zone": slot.get("zone", "salon"),
            }

        return list(by_time.values())

    def _candidate_slots(
        self,
        date: str,
        time: str,
        num_people: int,
        preferences: str,
        *,
        allow_reservation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        zone = self._normalize_zone(preferences)

        if allow_reservation_id:
            day_items = self.client.query(KeyConditionExpression=Key("PK").eq(self._slot_pk(date)))
            slots = [
                row
                for row in day_items
                if row.get("entity_type") == "slot"
                and row.get("time") == time
                and row.get("status") in {"open", "booked"}
            ]
        else:
            slots = [slot for slot in self._query_open_slots(date) if slot.get("time") == time]

        candidates: list[dict[str, Any]] = []
        for slot in slots:
            if int(slot.get("capacity_min", 1)) > num_people or int(slot.get("capacity_max", 99)) < num_people:
                continue
            if zone and slot.get("zone") != zone:
                continue
            if allow_reservation_id and slot.get("status") == "booked" and slot.get("reservation_id") != allow_reservation_id:
                continue
            if not allow_reservation_id and slot.get("status") != "open":
                continue
            candidates.append(slot)

        candidates.sort(
            key=lambda slot: (
                int(slot.get("priority", 99)),
                slot.get("table_id", ""),
            )
        )
        return candidates

    def _build_reservation_item(self, reservation: dict[str, Any]) -> dict[str, Any]:
        return {
            **self._reservation_key(reservation["id"]),
            "entity_type": "reservation",
            "id": reservation["id"],
            "status": reservation["status"],
            "date": reservation["date"],
            "time": reservation["time"],
            "num_people": int(reservation["num_people"]),
            "customer_name": reservation["customer_name"],
            "phone": reservation["phone"],
            "preferences": reservation.get("preferences", ""),
            "special_occasion": reservation.get("special_occasion", ""),
            "table_id": reservation.get("table_id", ""),
            "table_zone": reservation.get("table_zone", ""),
            "slot_ref": reservation.get("slot_ref", ""),
            "ttl": self._reservation_ttl(reservation["date"]),
            "created_at": reservation["created_at"],
            "updated_at": reservation["updated_at"],
            "GSI2PK": f"DATE#{reservation['date']}",
            "GSI2SK": f"TIME#{reservation['time']}#RES#{reservation['id']}",
            "GSI3PK": f"PHONE#{reservation['phone']}",
            "GSI3SK": f"DATE#{reservation['date']}#TIME#{reservation['time']}#RES#{reservation['id']}",
        }

    def _slot_update_to_booked(self, slot: dict[str, Any], reservation_id: str) -> dict[str, Any]:
        return {
            "Update": {
                "TableName": self.client.table.name,
                "Key": self.client.serialize_key({"PK": slot["PK"], "SK": slot["SK"]}),
                "UpdateExpression": (
                    "SET #status = :booked, reservation_id = :rid, hold_expires_at = :zero, "
                    "GSI1PK = :gsi_pk, updated_at = :updated_at, #version = #version + :inc"
                ),
                "ConditionExpression": "#status = :open OR reservation_id = :rid",
                "ExpressionAttributeNames": {"#status": "status", "#version": "version"},
                "ExpressionAttributeValues": self.client.serialize_expression_values(
                    {
                        ":open": "open",
                        ":booked": "booked",
                        ":rid": reservation_id,
                        ":zero": 0,
                        ":gsi_pk": f"DAY#{slot['date']}#STATUS#booked",
                        ":updated_at": self._now_iso(),
                        ":inc": 1,
                    }
                ),
            }
        }

    def _slot_update_to_open(self, slot: dict[str, Any], reservation_id: str) -> dict[str, Any]:
        return {
            "Update": {
                "TableName": self.client.table.name,
                "Key": self.client.serialize_key({"PK": slot["PK"], "SK": slot["SK"]}),
                "UpdateExpression": (
                    "SET #status = :open, reservation_id = :empty, hold_expires_at = :zero, "
                    "GSI1PK = :gsi_pk, updated_at = :updated_at, #version = #version + :inc"
                ),
                "ConditionExpression": "reservation_id = :rid",
                "ExpressionAttributeNames": {"#status": "status", "#version": "version"},
                "ExpressionAttributeValues": self.client.serialize_expression_values(
                    {
                        ":open": "open",
                        ":empty": "",
                        ":zero": 0,
                        ":gsi_pk": f"DAY#{slot['date']}#STATUS#open",
                        ":updated_at": self._now_iso(),
                        ":inc": 1,
                        ":rid": reservation_id,
                    }
                ),
            }
        }

    def get_reservation(self, reservation_id: str) -> dict[str, Any] | None:
        return self.client.get_item(self._reservation_key(reservation_id))

    def create_reservation(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        valid, error = self._validate_date_time(payload["date"], payload["time"])
        if not valid:
            return False, error, None

        num_people = int(payload["num_people"])
        if num_people < 1 or num_people > 12:
            return False, "El número de personas debe estar entre 1 y 12", None

        if not payload.get("customer_name", "").strip():
            return False, "Debes indicar el nombre del cliente", None

        if len(payload.get("phone", "").strip()) < 9:
            return False, "Debes indicar un teléfono válido", None

        candidates = self._candidate_slots(
            payload["date"],
            payload["time"],
            num_people,
            payload.get("preferences", ""),
        )
        if not candidates:
            return False, "No hay slots abiertos disponibles para ese horario", None

        selected = candidates[0]
        now_iso = self._now_iso()
        reservation_id = payload.get("id") or self._reservation_id(payload["date"])
        reservation = {
            "id": reservation_id,
            "status": payload.get("status", "pending").lower(),
            "date": payload["date"],
            "time": payload["time"],
            "num_people": num_people,
            "customer_name": payload["customer_name"].strip(),
            "phone": payload["phone"].strip(),
            "preferences": payload.get("preferences", "").strip(),
            "special_occasion": payload.get("special_occasion", "").strip(),
            "table_id": selected.get("table_id", ""),
            "table_zone": selected.get("zone", "salon"),
            "slot_ref": self._slot_ref(payload["date"], payload["time"], selected.get("table_id", "")),
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        if reservation["status"] not in VALID_STATUSES:
            return False, "Estado inválido. Usa pending, confirmed o cancelled", None

        transact_items: list[dict[str, Any]] = []

        if reservation["status"] in ACTIVE_STATUSES:
            transact_items.append(self._slot_update_to_booked(selected, reservation_id))

        transact_items.append(
            {
                "Put": {
                    "TableName": self.client.table.name,
                    "Item": self.client.serialize_item(self._build_reservation_item(reservation)),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            }
        )

        lookup = {
            **self._customer_lookup_key(
                reservation["phone"], reservation["date"], reservation["time"], reservation["id"]
            ),
            "entity_type": "customer_lookup",
            "reservation_id": reservation["id"],
            "status": reservation["status"],
            "date": reservation["date"],
            "time": reservation["time"],
            "ttl": self._reservation_ttl(reservation["date"], keep_days=30),
            "updated_at": now_iso,
        }
        transact_items.append(
            {
                "Put": {
                    "TableName": self.client.table.name,
                    "Item": self.client.serialize_item(lookup),
                }
            }
        )

        ok = self.client.transact_write(transact_items)
        if not ok:
            return False, "No se pudo crear la reserva. Puede que el slot ya se haya ocupado", None

        return True, "", reservation

    def list_reservations(
        self,
        *,
        date: str = "",
        status: str = "all",
        customer_name: str = "",
        phone: str = "",
    ) -> list[dict[str, Any]]:
        status = status.lower().strip() or "all"

        if date:
            rows = self.client.query(
                IndexName="GSI2",
                KeyConditionExpression=Key("GSI2PK").eq(f"DATE#{date}"),
            )
        elif phone:
            rows = self.client.query(
                IndexName="GSI3",
                KeyConditionExpression=Key("GSI3PK").eq(f"PHONE#{phone.strip()}"),
            )
        else:
            rows = self.client.scan(FilterExpression=Attr("entity_type").eq("reservation"))

        reservations = [row for row in rows if row.get("entity_type") == "reservation"]

        if status in VALID_STATUSES:
            reservations = [row for row in reservations if row.get("status") == status]

        if customer_name:
            query = customer_name.lower().strip()
            reservations = [row for row in reservations if query in row.get("customer_name", "").lower()]

        if phone and not date:
            phone_norm = phone.strip()
            reservations = [row for row in reservations if row.get("phone") == phone_norm]

        reservations.sort(key=lambda row: (row.get("date", ""), row.get("time", ""), row.get("id", "")))
        return reservations

    def query_reservations_by_date(self, date: str) -> list[dict[str, Any]]:
        return self.list_reservations(date=date)

    def scan_all_reservations(self) -> list[dict[str, Any]]:
        return self.list_reservations()

    def slot_stats(self, date: str) -> dict[str, int]:
        rows = self.client.query(KeyConditionExpression=Key("PK").eq(self._slot_pk(date)))
        slots = [row for row in rows if row.get("entity_type") == "slot"]
        counters = {"open": 0, "held": 0, "booked": 0, "blocked": 0}
        for row in slots:
            status = str(row.get("status", "")).lower()
            if status in counters:
                counters[status] += 1
        counters["total"] = len(slots)
        return counters

    def update_reservation(self, reservation_id: str, updates: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        current = self.get_reservation(reservation_id)
        if not current:
            return False, "No existe la reserva indicada", None

        merged = {**current, **updates}
        merged["id"] = reservation_id
        merged["status"] = str(merged.get("status", "pending")).lower()
        merged["num_people"] = int(merged.get("num_people", 0))

        if merged["status"] not in VALID_STATUSES:
            return False, "Estado inválido. Usa pending, confirmed o cancelled", None

        if merged["num_people"] < 1 or merged["num_people"] > 12:
            return False, "El número de personas debe estar entre 1 y 12", None

        if len(str(merged.get("phone", "")).strip()) < 9:
            return False, "Número de teléfono inválido", None

        now_iso = self._now_iso()
        merged["updated_at"] = now_iso

        current_slot = None
        if current.get("table_id"):
            current_slot = {
                "PK": self._slot_pk(str(current["date"])),
                "SK": self._slot_sk(str(current["time"]), str(current["table_id"])),
                "date": str(current["date"]),
                "time": str(current["time"]),
                "table_id": str(current["table_id"]),
            }

        needs_slot = merged["status"] in ACTIVE_STATUSES
        current_active = str(current.get("status", "")).lower() in ACTIVE_STATUSES
        slot_fields_changed = any(
            str(current.get(field, "")) != str(merged.get(field, ""))
            for field in ("date", "time", "num_people", "preferences")
        )
        status_changed = str(current.get("status", "")).lower() != merged["status"]

        replacement_slot: dict[str, Any] | None = None
        if needs_slot and (slot_fields_changed or status_changed):
            valid, error = self._validate_date_time(str(merged["date"]), str(merged["time"]))
            if not valid:
                return False, error, None

            candidates = self._candidate_slots(
                str(merged["date"]),
                str(merged["time"]),
                merged["num_people"],
                str(merged.get("preferences", "")),
                allow_reservation_id=reservation_id,
            )
            if not candidates:
                return False, "No hay disponibilidad para los cambios solicitados", None
            replacement_slot = candidates[0]
            merged["table_id"] = replacement_slot.get("table_id", "")
            merged["table_zone"] = replacement_slot.get("zone", "salon")
            merged["slot_ref"] = self._slot_ref(
                str(merged["date"]),
                str(merged["time"]),
                str(replacement_slot.get("table_id", "")),
            )

        if not needs_slot:
            merged["table_id"] = ""
            merged["table_zone"] = ""
            merged["slot_ref"] = ""

        transact_items: list[dict[str, Any]] = []
        slot_changed = False
        if replacement_slot and current_slot:
            slot_changed = (
                current_slot["PK"] != replacement_slot["PK"]
                or current_slot["SK"] != replacement_slot["SK"]
            )
        elif replacement_slot and not current_slot:
            slot_changed = True

        if current_slot and current_active and (not needs_slot or slot_changed):
            transact_items.append(self._slot_update_to_open(current_slot, reservation_id))

        if needs_slot and replacement_slot:
            same_slot = (
                current_slot
                and current_slot["PK"] == replacement_slot["PK"]
                and current_slot["SK"] == replacement_slot["SK"]
            )
            if not same_slot or str(current.get("status")) not in ACTIVE_STATUSES:
                transact_items.append(self._slot_update_to_booked(replacement_slot, reservation_id))

        transact_items.append(
            {
                "Put": {
                    "TableName": self.client.table.name,
                    "Item": self.client.serialize_item(self._build_reservation_item(merged)),
                    "ConditionExpression": "attribute_exists(PK)",
                }
            }
        )

        old_lookup_key = self._customer_lookup_key(
            str(current["phone"]), str(current["date"]), str(current["time"]), reservation_id
        )
        new_lookup_item = {
            **self._customer_lookup_key(
                str(merged["phone"]), str(merged["date"]), str(merged["time"]), reservation_id
            ),
            "entity_type": "customer_lookup",
            "reservation_id": reservation_id,
            "status": merged["status"],
            "date": str(merged["date"]),
            "time": str(merged["time"]),
            "ttl": self._reservation_ttl(str(merged["date"]), keep_days=30),
            "updated_at": now_iso,
        }

        transact_items.append(
            {
                "Delete": {
                    "TableName": self.client.table.name,
                    "Key": self.client.serialize_key(old_lookup_key),
                }
            }
        )
        transact_items.append(
            {
                "Put": {
                    "TableName": self.client.table.name,
                    "Item": self.client.serialize_item(new_lookup_item),
                }
            }
        )

        ok = self.client.transact_write(transact_items)
        if not ok:
            return False, "No se pudo actualizar la reserva por conflicto de slots", None

        return True, "", merged

    def cancel_reservation(self, reservation_id: str) -> tuple[bool, str, dict[str, Any] | None]:
        return self.update_reservation(reservation_id, {"status": "cancelled"})


booking_repository = BookingV2Repository()
