"""
Servicio de reservas con DynamoDB usando slots de 30 minutos por mesa.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time as time_type, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


ENTITY_TABLE = "TABLE"
ENTITY_SLOT = "SLOT"
ENTITY_RESERVATION = "RESERVATION"

SLOT_OPEN = "OPEN"
SLOT_BOOKED = "BOOKED"

RESERVATION_ACTIVE = "ACTIVE"
RESERVATION_CANCELLED = "CANCELLED"

PHONE_GSI_NAME = "gsi1"


class ReservationError(Exception):
    """Error de dominio para operaciones de reserva."""


class ConfigurationError(ReservationError):
    """Error de configuración del sistema de reservas."""


class NoAvailabilityError(ReservationError):
    """No hay disponibilidad para la solicitud."""


class ReservationNotFoundError(ReservationError):
    """No se encontró una reserva."""


class ReservationConflictError(ReservationError):
    """Conflicto de concurrencia o estado al reservar/cancelar."""


@dataclass(frozen=True)
class TableLayout:
    table_id: str
    label: str
    capacity_min: int
    capacity_max: int
    area: str = "interior"
    is_active: bool = True


DEFAULT_TABLE_LAYOUT: list[TableLayout] = [
    TableLayout(table_id="M01", label="Mesa 1", capacity_min=1, capacity_max=2, area="interior"),
    TableLayout(table_id="M02", label="Mesa 2", capacity_min=1, capacity_max=2, area="interior"),
    TableLayout(table_id="M03", label="Mesa 3", capacity_min=2, capacity_max=4, area="interior"),
    TableLayout(table_id="M04", label="Mesa 4", capacity_min=2, capacity_max=4, area="interior"),
    TableLayout(table_id="M05", label="Mesa 5", capacity_min=2, capacity_max=4, area="terraza"),
    TableLayout(table_id="M06", label="Mesa 6", capacity_min=2, capacity_max=4, area="terraza"),
    TableLayout(table_id="M07", label="Mesa 7", capacity_min=4, capacity_max=6, area="interior"),
    TableLayout(table_id="M08", label="Mesa 8", capacity_min=4, capacity_max=6, area="interior"),
    TableLayout(table_id="M09", label="Mesa 9", capacity_min=4, capacity_max=8, area="interior"),
    TableLayout(table_id="M10", label="Mesa 10", capacity_min=4, capacity_max=8, area="terraza"),
]


# Horarios basados en el prompt del restaurante
WEEKLY_SERVICE_WINDOWS: dict[int, list[tuple[str, str]]] = {
    0: [],  # lunes cerrado
    1: [("13:00", "16:00"), ("20:00", "23:30")],  # martes
    2: [("13:00", "16:00"), ("20:00", "23:30")],  # miércoles
    3: [("13:00", "16:00"), ("20:00", "23:30")],  # jueves
    4: [("13:00", "16:00"), ("20:00", "00:00")],  # viernes
    5: [("13:00", "16:00"), ("20:00", "00:00")],  # sábado
    6: [("13:00", "17:00")],  # domingo
}


class DynamoReservationRepository:
    """Repositorio de reservas con single-table design en DynamoDB."""

    def __init__(
        self,
        *,
        table_name: str,
        region_name: str,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        aws_session_token: str = "",
    ):
        session_kwargs: dict[str, Any] = {
            "region_name": region_name,
            "aws_access_key_id": aws_access_key_id,
            "aws_secret_access_key": aws_secret_access_key,
        }
        if aws_session_token:
            session_kwargs["aws_session_token"] = aws_session_token

        self._dynamodb = boto3.resource("dynamodb", **session_kwargs)
        self._client = boto3.client("dynamodb", **session_kwargs)
        self._table = self._dynamodb.Table(table_name)
        self._table_name = table_name
        self._region_name = region_name
        self._serializer = TypeSerializer()

    def _raise_if_table_not_found(self, exc: ClientError) -> None:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            raise ConfigurationError(
                f"La tabla DynamoDB '{self._table_name}' no existe en región '{self._region_name}'. "
                "Revisa DYNAMODB_TABLE_NAME y DYNAMODB_REGION."
            ) from exc

    @staticmethod
    def _table_pk(table_id: str) -> str:
        return f"TABLE#{table_id}"

    @staticmethod
    def _slot_pk(service_date: str) -> str:
        return f"SLOT#{service_date}"

    @staticmethod
    def _slot_sk(start_time: str, table_id: str) -> str:
        return f"{start_time}#TABLE#{table_id}"

    @staticmethod
    def _reservation_pk(reservation_id: str) -> str:
        return f"RESERVATION#{reservation_id}"

    @staticmethod
    def _serialize_dict(data: dict[str, Any], serializer: TypeSerializer) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for key, value in data.items():
            serialized[key] = serializer.serialize(value)
        return serialized

    def list_active_tables(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {
            "FilterExpression": Attr("entity_type").eq(ENTITY_TABLE) & Attr("is_active").eq(True),
        }

        while True:
            try:
                response = self._table.scan(**scan_kwargs)
            except ClientError as exc:
                self._raise_if_table_not_found(exc)
                raise
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        items.sort(key=lambda item: item.get("table_id", ""))
        return items

    def seed_tables_if_empty(self, default_layout: list[TableLayout], *, now_iso: str) -> dict[str, int]:
        existing = self.list_active_tables()
        if existing:
            return {"seeded": 0, "existing": len(existing)}

        seeded = 0
        for table in default_layout:
            item = {
                "PK": self._table_pk(table.table_id),
                "SK": "PROFILE",
                "entity_type": ENTITY_TABLE,
                "table_id": table.table_id,
                "label": table.label,
                "capacity_min": int(table.capacity_min),
                "capacity_max": int(table.capacity_max),
                "area": table.area,
                "is_active": bool(table.is_active),
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            try:
                self._table.put_item(
                    Item=item,
                    ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
                )
                seeded += 1
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code != "ConditionalCheckFailedException":
                    raise

        return {"seeded": seeded, "existing": 0}

    def slots_exist_for_date(self, service_date: str) -> bool:
        try:
            response = self._table.query(
                KeyConditionExpression=Key("PK").eq(self._slot_pk(service_date)),
                Select="COUNT",
                Limit=1,
            )
        except ClientError as exc:
            self._raise_if_table_not_found(exc)
            raise
        return int(response.get("Count", 0)) > 0

    def publish_slots_for_date(
        self,
        *,
        service_date: str,
        slot_minutes: int,
        service_windows: list[tuple[str, str]],
        timezone_name: str,
        now_iso: str,
    ) -> dict[str, int]:
        tables = self.list_active_tables()
        if not tables:
            raise ConfigurationError("No hay mesas activas configuradas para publicar slots.")

        if not service_windows:
            return {"inserted": 0, "existing": 0}

        timezone = ZoneInfo(timezone_name)
        slot_delta = timedelta(minutes=slot_minutes)
        day = datetime.strptime(service_date, "%Y-%m-%d").date()
        inserted = 0
        existing = 0

        for window_start, window_end in service_windows:
            start_dt = datetime.combine(day, time_type.fromisoformat(window_start), tzinfo=timezone)
            end_dt = datetime.combine(day, time_type.fromisoformat(window_end), tzinfo=timezone)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            cursor = start_dt
            while cursor + slot_delta <= end_dt:
                slot_start = cursor.strftime("%H:%M")
                slot_end = (cursor + slot_delta).strftime("%H:%M")

                for table in tables:
                    table_id = table["table_id"]
                    item = {
                        "PK": self._slot_pk(service_date),
                        "SK": self._slot_sk(slot_start, table_id),
                        "entity_type": ENTITY_SLOT,
                        "date": service_date,
                        "start_time": slot_start,
                        "end_time": slot_end,
                        "table_id": table_id,
                        "table_label": table.get("label", table_id),
                        "capacity_min": int(table["capacity_min"]),
                        "capacity_max": int(table["capacity_max"]),
                        "area": table.get("area", "interior"),
                        "status": SLOT_OPEN,
                        "created_at": now_iso,
                        "updated_at": now_iso,
                    }

                    try:
                        self._table.put_item(
                            Item=item,
                            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        )
                        inserted += 1
                    except ClientError as exc:
                        self._raise_if_table_not_found(exc)
                        code = exc.response.get("Error", {}).get("Code", "")
                        if code == "ConditionalCheckFailedException":
                            existing += 1
                            continue
                        raise

                cursor += slot_delta

        return {"inserted": inserted, "existing": existing}

    def query_available_slots(
        self,
        *,
        service_date: str,
        party_size: int,
        preferred_time: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        key_condition = Key("PK").eq(self._slot_pk(service_date))
        if preferred_time:
            key_condition = key_condition & Key("SK").begins_with(f"{preferred_time}#")

        filter_expression = (
            Attr("entity_type").eq(ENTITY_SLOT)
            & Attr("status").eq(SLOT_OPEN)
            & Attr("capacity_min").lte(int(party_size))
            & Attr("capacity_max").gte(int(party_size))
        )

        query_kwargs: dict[str, Any] = {
            "KeyConditionExpression": key_condition,
            "FilterExpression": filter_expression,
            "ScanIndexForward": True,
        }

        results: list[dict[str, Any]] = []
        while True:
            try:
                response = self._table.query(**query_kwargs)
            except ClientError as exc:
                self._raise_if_table_not_found(exc)
                raise
            results.extend(response.get("Items", []))
            if len(results) >= limit:
                break
            if "LastEvaluatedKey" not in response:
                break
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        results.sort(
            key=lambda item: (
                item.get("start_time", ""),
                int(item.get("capacity_max", 0)),
                item.get("table_id", ""),
            )
        )
        return results[:limit]

    def create_reservation_transaction(
        self,
        *,
        slots: list[dict[str, Any]],
        reservation_id: str,
        phone: str,
        customer_name: str,
        party_size: int,
        notes: str,
        duration_minutes: int,
        now_iso: str,
    ) -> dict[str, Any]:
        if not slots:
            raise ReservationError("No se recibieron slots para crear la reserva.")
        first_slot = slots[0]
        last_slot = slots[-1]
        booked_slots = [
            {
                "pk": slot["PK"],
                "sk": slot["SK"],
                "start_time": slot["start_time"],
                "end_time": slot["end_time"],
            }
            for slot in slots
        ]

        reservation_item = {
            "PK": self._reservation_pk(reservation_id),
            "SK": "META",
            "entity_type": ENTITY_RESERVATION,
            "reservation_id": reservation_id,
            "phone": phone,
            "customer_name": customer_name,
            "party_size": int(party_size),
            "date": first_slot["date"],
            "time": first_slot["start_time"],
            "end_time": last_slot["end_time"],
            "duration_minutes": int(duration_minutes),
            "table_id": first_slot["table_id"],
            "table_label": first_slot.get("table_label", first_slot["table_id"]),
            "area": first_slot.get("area", "interior"),
            "status": RESERVATION_ACTIVE,
            "notes": notes,
            "slot_pk": first_slot["PK"],
            "slot_sk": first_slot["SK"],
            "booked_slots": booked_slots,
            "GSI1PK": f"PHONE#{phone}",
            "GSI1SK": f"RES#{now_iso}#{reservation_id}",
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        transact_items: list[dict[str, Any]] = []
        for slot in slots:
            transact_items.append(
                {
                    "Update": {
                        "TableName": self._table_name,
                        "Key": self._serialize_dict(
                            {
                                "PK": slot["PK"],
                                "SK": slot["SK"],
                            },
                            self._serializer,
                        ),
                        "UpdateExpression": (
                            "SET #status = :booked, reservation_id = :reservation_id, "
                            "phone = :phone, customer_name = :customer_name, "
                            "party_size = :party_size, booked_at = :booked_at, updated_at = :updated_at"
                        ),
                        "ConditionExpression": "#status = :open AND attribute_not_exists(reservation_id)",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": self._serialize_dict(
                            {
                                ":open": SLOT_OPEN,
                                ":booked": SLOT_BOOKED,
                                ":reservation_id": reservation_id,
                                ":phone": phone,
                                ":customer_name": customer_name,
                                ":party_size": int(party_size),
                                ":booked_at": now_iso,
                                ":updated_at": now_iso,
                            },
                            self._serializer,
                        ),
                    }
                }
            )

        transact_items.append(
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": self._serialize_dict(reservation_item, self._serializer),
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            }
        )

        try:
            self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as exc:
            self._raise_if_table_not_found(exc)
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
                raise ReservationConflictError("El slot dejó de estar disponible. Intenta otra franja.") from exc
            raise

        return reservation_item

    def get_reservation(self, reservation_id: str) -> dict[str, Any] | None:
        try:
            response = self._table.get_item(
                Key={
                    "PK": self._reservation_pk(reservation_id),
                    "SK": "META",
                }
            )
        except ClientError as exc:
            self._raise_if_table_not_found(exc)
            raise
        return response.get("Item")

    def list_reservations_by_phone(
        self,
        *,
        phone: str,
        only_active: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        query_kwargs: dict[str, Any] = {
            "IndexName": PHONE_GSI_NAME,
            "KeyConditionExpression": Key("GSI1PK").eq(f"PHONE#{phone}"),
            "ScanIndexForward": False,
        }
        if only_active:
            query_kwargs["FilterExpression"] = Attr("status").eq(RESERVATION_ACTIVE)

        items: list[dict[str, Any]] = []
        while True:
            try:
                response = self._table.query(**query_kwargs)
            except ClientError as exc:
                self._raise_if_table_not_found(exc)
                raise
            items.extend(response.get("Items", []))
            if len(items) >= limit:
                break
            if "LastEvaluatedKey" not in response:
                break
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        return items[:limit]

    def cancel_reservation_transaction(self, *, reservation: dict[str, Any], now_iso: str) -> dict[str, Any]:
        booked_slots = reservation.get("booked_slots") or []
        if not booked_slots and reservation.get("slot_pk") and reservation.get("slot_sk"):
            booked_slots = [
                {
                    "pk": reservation["slot_pk"],
                    "sk": reservation["slot_sk"],
                }
            ]
        if not booked_slots:
            raise ReservationConflictError("La reserva no contiene referencias de slots para liberar.")

        transact_items: list[dict[str, Any]] = [
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": self._serialize_dict(
                        {
                            "PK": reservation["PK"],
                            "SK": reservation["SK"],
                        },
                        self._serializer,
                    ),
                    "UpdateExpression": (
                        "SET #status = :cancelled, cancelled_at = :cancelled_at, updated_at = :updated_at"
                    ),
                    "ConditionExpression": "#status = :active",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": self._serialize_dict(
                        {
                            ":active": RESERVATION_ACTIVE,
                            ":cancelled": RESERVATION_CANCELLED,
                            ":cancelled_at": now_iso,
                            ":updated_at": now_iso,
                        },
                        self._serializer,
                    ),
                }
            }
        ]

        for slot_ref in booked_slots:
            transact_items.append(
                {
                    "Update": {
                        "TableName": self._table_name,
                        "Key": self._serialize_dict(
                            {
                                "PK": slot_ref["pk"],
                                "SK": slot_ref["sk"],
                            },
                            self._serializer,
                        ),
                        "UpdateExpression": (
                            "SET #status = :open, updated_at = :updated_at "
                            "REMOVE reservation_id, phone, customer_name, party_size, booked_at"
                        ),
                        "ConditionExpression": "#status = :booked AND reservation_id = :reservation_id",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": self._serialize_dict(
                            {
                                ":booked": SLOT_BOOKED,
                                ":open": SLOT_OPEN,
                                ":reservation_id": reservation["reservation_id"],
                                ":updated_at": now_iso,
                            },
                            self._serializer,
                        ),
                    }
                }
            )

        try:
            self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as exc:
            self._raise_if_table_not_found(exc)
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
                raise ReservationConflictError(
                    "No se pudo cancelar de forma segura; la reserva ya pudo cambiar de estado."
                ) from exc
            raise

        updated = dict(reservation)
        updated["status"] = RESERVATION_CANCELLED
        updated["cancelled_at"] = now_iso
        updated["updated_at"] = now_iso
        return updated


class ReservationService:
    """Servicio de dominio para publicar slots y gestionar reservas."""

    def __init__(
        self,
        *,
        repository: DynamoReservationRepository,
        timezone_name: str = "Europe/Madrid",
        slot_minutes: int = 30,
        reservation_duration_minutes: int = 90,
        auto_seed_tables: bool = True,
        auto_publish_slots: bool = True,
    ):
        if slot_minutes <= 0:
            raise ConfigurationError("slot_minutes debe ser mayor que cero.")
        if reservation_duration_minutes <= 0:
            raise ConfigurationError("reservation_duration_minutes debe ser mayor que cero.")
        if reservation_duration_minutes % slot_minutes != 0:
            raise ConfigurationError(
                "reservation_duration_minutes debe ser múltiplo de slot_minutes para bloquear slots enteros."
            )

        required_slot_count = reservation_duration_minutes // slot_minutes
        # DynamoDB soporta máximo 25 operaciones por transacción.
        if required_slot_count + 1 > 25:
            raise ConfigurationError("Duración de reserva demasiado alta para una transacción DynamoDB.")

        self._repository = repository
        self._timezone_name = timezone_name
        self._timezone = ZoneInfo(timezone_name)
        self._slot_minutes = slot_minutes
        self._reservation_duration_minutes = reservation_duration_minutes
        self._required_slot_count = required_slot_count
        self._auto_seed_tables = auto_seed_tables
        self._auto_publish_slots = auto_publish_slots

    @staticmethod
    def normalize_phone(phone: str) -> str:
        normalized = re.sub(r"[^0-9]", "", phone or "")
        if not normalized:
            raise ReservationError("El teléfono es obligatorio para gestionar reservas.")
        return normalized

    @staticmethod
    def _parse_date(value: str) -> date_type:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except Exception as exc:
            raise ReservationError("La fecha debe ir en formato YYYY-MM-DD.") from exc

    @staticmethod
    def _parse_time(value: str) -> time_type:
        try:
            return time_type.fromisoformat(value)
        except Exception as exc:
            raise ReservationError("La hora debe ir en formato HH:MM (24h).") from exc

    def _now_iso(self) -> str:
        return datetime.now(self._timezone).isoformat(timespec="seconds")

    def _service_windows(self, day: date_type) -> list[tuple[str, str]]:
        return WEEKLY_SERVICE_WINDOWS.get(day.weekday(), [])

    def _time_plus_minutes(self, hhmm: str, minutes: int) -> str:
        base_dt = datetime.strptime(hhmm, "%H:%M")
        return (base_dt + timedelta(minutes=minutes)).strftime("%H:%M")

    def _required_times_for_start(self, start_time: str) -> list[str]:
        return [
            self._time_plus_minutes(start_time, i * self._slot_minutes)
            for i in range(self._required_slot_count)
        ]

    def _build_contiguous_sequences(
        self,
        slots: list[dict[str, Any]],
        *,
        candidate_starts: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        by_table: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for slot in slots:
            by_table[slot["table_id"]][slot["start_time"]] = slot

        if candidate_starts is None:
            candidate_starts = sorted({slot["start_time"] for slot in slots})

        sequences_by_start: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for start_time in candidate_starts:
            required_times = self._required_times_for_start(start_time)
            for table_id, slot_map in by_table.items():
                sequence_slots: list[dict[str, Any]] = []
                all_present = True
                for required_time in required_times:
                    slot = slot_map.get(required_time)
                    if not slot:
                        all_present = False
                        break
                    sequence_slots.append(slot)
                if not all_present:
                    continue

                first = sequence_slots[0]
                last = sequence_slots[-1]
                sequences_by_start[start_time].append(
                    {
                        "table_id": table_id,
                        "table_label": first.get("table_label", table_id),
                        "capacity_min": int(first.get("capacity_min", 0)),
                        "capacity_max": int(first.get("capacity_max", 0)),
                        "area": first.get("area", ""),
                        "start_time": start_time,
                        "end_time": last.get("end_time", self._time_plus_minutes(start_time, self._slot_minutes)),
                        "slots": sequence_slots,
                    }
                )

        return sequences_by_start

    def _ensure_day_ready(self, service_date: str) -> None:
        day = self._parse_date(service_date)
        now_iso = self._now_iso()

        if self._auto_seed_tables:
            self._repository.seed_tables_if_empty(DEFAULT_TABLE_LAYOUT, now_iso=now_iso)

        tables = self._repository.list_active_tables()
        if not tables:
            raise ConfigurationError(
                "No hay mesas configuradas. Crea mesas en DynamoDB antes de operar reservas."
            )

        if not self._auto_publish_slots:
            return

        if self._repository.slots_exist_for_date(service_date):
            return

        service_windows = self._service_windows(day)
        if not service_windows:
            raise NoAvailabilityError("El restaurante está cerrado en esa fecha.")

        publish_result = self._repository.publish_slots_for_date(
            service_date=service_date,
            slot_minutes=self._slot_minutes,
            service_windows=service_windows,
            timezone_name=self._timezone_name,
            now_iso=now_iso,
        )
        logger.info(
            "Slots publicados para %s -> insertados=%s existentes=%s",
            service_date,
            publish_result["inserted"],
            publish_result["existing"],
        )

    def publish_slots_for_date(self, service_date: str) -> dict[str, Any]:
        day = self._parse_date(service_date)
        service_windows = self._service_windows(day)
        if not service_windows:
            return {
                "date": service_date,
                "published": False,
                "message": "Restaurante cerrado en esta fecha.",
            }
        now_iso = self._now_iso()
        seeded = self._repository.seed_tables_if_empty(DEFAULT_TABLE_LAYOUT, now_iso=now_iso)
        result = self._repository.publish_slots_for_date(
            service_date=service_date,
            slot_minutes=self._slot_minutes,
            service_windows=service_windows,
            timezone_name=self._timezone_name,
            now_iso=now_iso,
        )
        return {
            "date": service_date,
            "seeded_tables": seeded,
            "published": True,
            "inserted_slots": result["inserted"],
            "existing_slots": result["existing"],
        }

    def check_availability(
        self,
        *,
        service_date: str,
        party_size: int,
        preferred_time: str | None = None,
    ) -> dict[str, Any]:
        if party_size <= 0:
            raise ReservationError("El número de personas debe ser mayor que cero.")

        service_date = self._parse_date(service_date).isoformat()
        if preferred_time:
            preferred_time = self._parse_time(preferred_time).strftime("%H:%M")

        self._ensure_day_ready(service_date)

        slots = self._repository.query_available_slots(
            service_date=service_date,
            party_size=party_size,
            preferred_time=None,
            limit=5000,
        )

        candidate_starts = [preferred_time] if preferred_time else None
        grouped = self._build_contiguous_sequences(slots, candidate_starts=candidate_starts)

        times: list[dict[str, Any]] = []
        for start_time in sorted(grouped.keys()):
            options = grouped[start_time]
            times.append(
                {
                    "time": start_time,
                    "end_time": options[0]["end_time"] if options else "",
                    "free_tables": len(options),
                    "example_tables": [slot["table_label"] for slot in options[:3]],
                }
            )

        total_bookable_options = sum(item["free_tables"] for item in times)

        return {
            "date": service_date,
            "party_size": int(party_size),
            "preferred_time": preferred_time,
            "duration_minutes": self._reservation_duration_minutes,
            "available": total_bookable_options > 0,
            "total_open_slots": total_bookable_options,
            "total_bookable_options": total_bookable_options,
            "times": times[:12],
        }

    def create_reservation(
        self,
        *,
        phone: str,
        customer_name: str,
        service_date: str,
        service_time: str,
        party_size: int,
        notes: str = "",
    ) -> dict[str, Any]:
        if not customer_name.strip():
            raise ReservationError("El nombre del cliente es obligatorio.")
        if party_size <= 0:
            raise ReservationError("El número de personas debe ser mayor que cero.")

        normalized_phone = self.normalize_phone(phone)
        service_date = self._parse_date(service_date).isoformat()
        service_time = self._parse_time(service_time).strftime("%H:%M")

        # Evita duplicados por reintentos o mensajes repetidos en la misma franja.
        existing_for_phone = self._repository.list_reservations_by_phone(
            phone=normalized_phone,
            only_active=True,
            limit=20,
        )
        for reservation in existing_for_phone:
            if reservation.get("date") == service_date and reservation.get("time") == service_time:
                return {
                    "reservation_id": reservation["reservation_id"],
                    "status": reservation["status"],
                    "name": reservation.get("customer_name", customer_name.strip()),
                    "phone": normalized_phone,
                    "date": reservation["date"],
                    "time": reservation["time"],
                    "end_time": reservation.get(
                        "end_time",
                        self._time_plus_minutes(reservation["time"], self._reservation_duration_minutes),
                    ),
                    "duration_minutes": int(
                        reservation.get("duration_minutes", self._reservation_duration_minutes)
                    ),
                    "party_size": int(reservation.get("party_size", party_size)),
                    "table": reservation.get("table_label", reservation.get("table_id", "")),
                    "area": reservation.get("area", ""),
                    "notes": reservation.get("notes", notes.strip()),
                    "already_exists": True,
                }

        self._ensure_day_ready(service_date)
        all_open_slots = self._repository.query_available_slots(
            service_date=service_date,
            party_size=party_size,
            preferred_time=None,
            limit=5000,
        )
        sequences_at_time = self._build_contiguous_sequences(
            all_open_slots,
            candidate_starts=[service_time],
        ).get(service_time, [])

        if not sequences_at_time:
            alternatives = self.check_availability(
                service_date=service_date,
                party_size=party_size,
                preferred_time=None,
            )
            raise NoAvailabilityError(
                f"No hay disponibilidad para {service_date} a las {service_time}. "
                f"Alternativas: {[item['time'] for item in alternatives.get('times', [])[:5]]}"
            )

        sequences_at_time.sort(
            key=lambda sequence: (
                int(sequence.get("capacity_max", 0)),
                int(sequence.get("capacity_min", 0)),
                sequence.get("table_id", ""),
            )
        )
        chosen = sequences_at_time[0]
        reservation_id = f"rsv_{datetime.now(self._timezone).strftime('%Y%m%d')}_{uuid4().hex[:8]}"

        saved = self._repository.create_reservation_transaction(
            slots=chosen["slots"],
            reservation_id=reservation_id,
            phone=normalized_phone,
            customer_name=customer_name.strip(),
            party_size=party_size,
            notes=notes.strip(),
            duration_minutes=self._reservation_duration_minutes,
            now_iso=self._now_iso(),
        )

        return {
            "reservation_id": saved["reservation_id"],
            "status": saved["status"],
            "name": saved["customer_name"],
            "phone": saved["phone"],
            "date": saved["date"],
            "time": saved["time"],
            "end_time": saved.get("end_time", self._time_plus_minutes(saved["time"], self._reservation_duration_minutes)),
            "duration_minutes": int(saved.get("duration_minutes", self._reservation_duration_minutes)),
            "party_size": int(saved["party_size"]),
            "table": saved.get("table_label", saved["table_id"]),
            "area": saved.get("area", ""),
            "notes": saved.get("notes", ""),
        }

    def list_reservations(
        self,
        *,
        phone: str,
        only_active: bool = True,
        limit: int = 10,
    ) -> dict[str, Any]:
        normalized_phone = self.normalize_phone(phone)
        safe_limit = max(1, min(limit, 25))
        rows = self._repository.list_reservations_by_phone(
            phone=normalized_phone,
            only_active=only_active,
            limit=safe_limit,
        )
        reservations = []
        for row in rows:
            reservations.append(
                {
                    "reservation_id": row.get("reservation_id", ""),
                    "status": row.get("status", ""),
                    "name": row.get("customer_name", ""),
                    "date": row.get("date", ""),
                    "time": row.get("time", ""),
                    "end_time": row.get("end_time", ""),
                    "duration_minutes": int(row.get("duration_minutes", self._reservation_duration_minutes)),
                    "party_size": int(row.get("party_size", 0)),
                    "table": row.get("table_label", row.get("table_id", "")),
                }
            )

        return {
            "phone": normalized_phone,
            "only_active": only_active,
            "count": len(reservations),
            "reservations": reservations,
        }

    def cancel_reservation(
        self,
        *,
        phone: str,
        reservation_id: str | None = None,
        service_date: str | None = None,
        service_time: str | None = None,
    ) -> dict[str, Any]:
        normalized_phone = self.normalize_phone(phone)

        target: dict[str, Any] | None = None
        if reservation_id:
            target = self._repository.get_reservation(reservation_id.strip())
            if not target or target.get("status") != RESERVATION_ACTIVE:
                raise ReservationNotFoundError("No encontré una reserva activa con ese ID.")
            if target.get("phone") != normalized_phone:
                raise ReservationNotFoundError("La reserva indicada no pertenece a este teléfono.")
        else:
            reservations = self._repository.list_reservations_by_phone(
                phone=normalized_phone,
                only_active=True,
                limit=20,
            )
            if service_date:
                service_date = self._parse_date(service_date).isoformat()
                reservations = [item for item in reservations if item.get("date") == service_date]
            if service_time:
                service_time = self._parse_time(service_time).strftime("%H:%M")
                reservations = [item for item in reservations if item.get("time") == service_time]

            if not reservations:
                raise ReservationNotFoundError("No encontré reservas activas que coincidan con tu solicitud.")

            if len(reservations) > 1:
                options = [f"{item.get('date')} {item.get('time')}" for item in reservations[:5]]
                raise ReservationConflictError(
                    "Tienes varias reservas activas. Indica fecha/hora exacta o reservation_id. "
                    f"Opciones: {options}"
                )
            target = reservations[0]

        cancelled = self._repository.cancel_reservation_transaction(
            reservation=target,
            now_iso=self._now_iso(),
        )
        return {
            "reservation_id": cancelled["reservation_id"],
            "status": cancelled["status"],
            "name": cancelled.get("customer_name", ""),
            "date": cancelled.get("date", ""),
            "time": cancelled.get("time", ""),
            "end_time": cancelled.get("end_time", ""),
            "duration_minutes": int(cancelled.get("duration_minutes", self._reservation_duration_minutes)),
            "party_size": int(cancelled.get("party_size", 0)),
            "table": cancelled.get("table_label", cancelled.get("table_id", "")),
            "cancelled_at": cancelled.get("cancelled_at", ""),
        }
