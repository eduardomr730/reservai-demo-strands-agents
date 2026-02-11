"""
Cliente de DynamoDB para gestionar reservas con asignación de mesas y slots.
"""
import boto3
import logging
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


DEFAULT_TABLE_LAYOUT = [
    {"id": "M01", "zone": "interior", "capacity_min": 1, "capacity_max": 2, "priority": 1, "is_active": True},
    {"id": "M02", "zone": "interior", "capacity_min": 1, "capacity_max": 2, "priority": 2, "is_active": True},
    {"id": "M03", "zone": "interior", "capacity_min": 2, "capacity_max": 4, "priority": 1, "is_active": True},
    {"id": "M04", "zone": "interior", "capacity_min": 2, "capacity_max": 4, "priority": 2, "is_active": True},
    {"id": "M05", "zone": "interior", "capacity_min": 4, "capacity_max": 6, "priority": 1, "is_active": True},
    {"id": "M06", "zone": "interior", "capacity_min": 6, "capacity_max": 8, "priority": 1, "is_active": True},
    {"id": "T01", "zone": "terraza", "capacity_min": 1, "capacity_max": 2, "priority": 1, "is_active": True},
    {"id": "T02", "zone": "terraza", "capacity_min": 1, "capacity_max": 2, "priority": 2, "is_active": True},
    {"id": "T03", "zone": "terraza", "capacity_min": 2, "capacity_max": 4, "priority": 1, "is_active": True},
    {"id": "T04", "zone": "terraza", "capacity_min": 2, "capacity_max": 4, "priority": 2, "is_active": True},
    {"id": "T05", "zone": "terraza", "capacity_min": 4, "capacity_max": 6, "priority": 1, "is_active": True},
    {"id": "T06", "zone": "terraza", "capacity_min": 6, "capacity_max": 8, "priority": 1, "is_active": True},
]


class DynamoDBClient:
    """Cliente para interactuar con DynamoDB."""

    SLOT_MINUTES = 30

    def __init__(self):
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=settings.dynamodb_region,
        )
        self.table = self.dynamodb.Table(settings.dynamodb_table_name)
        self._ddb_client = self.dynamodb.meta.client
        self._serializer = TypeSerializer()
        logger.info(f"✅ DynamoDB client inicializado para tabla: {settings.dynamodb_table_name}")
        self._ensure_table_catalog()

    def _python_to_dynamodb(self, data: dict) -> dict:
        """Convierte tipos Python a tipos DynamoDB (int -> Decimal)."""
        converted = {}
        for key, value in data.items():
            if isinstance(value, int):
                converted[key] = Decimal(str(value))
            elif isinstance(value, float):
                converted[key] = Decimal(str(value))
            elif isinstance(value, dict):
                converted[key] = self._python_to_dynamodb(value)
            else:
                converted[key] = value
        return converted

    def _dynamodb_to_python(self, data: dict) -> dict:
        """Convierte tipos DynamoDB a tipos Python (Decimal -> int/float)."""
        converted = {}
        for key, value in data.items():
            if isinstance(value, Decimal):
                converted[key] = int(value) if value % 1 == 0 else float(value)
            elif isinstance(value, dict):
                converted[key] = self._dynamodb_to_python(value)
            else:
                converted[key] = value
        return converted

    def _serialize_item(self, item: dict) -> dict:
        serialized = {}
        for key, value in item.items():
            if value is None:
                continue
            if key in {"PK", "SK", "GSI1PK", "GSI1SK"}:
                serialized[key] = {"S": str(value)}
                continue
            serialized[key] = self._serializer.serialize(value)
        return serialized

    def _serialize_value(self, value):
        return self._serializer.serialize(value)

    def _strip_internal_fields(self, item: dict) -> dict:
        item.pop("PK", None)
        item.pop("SK", None)
        item.pop("GSI1PK", None)
        item.pop("GSI1SK", None)
        return item

    def _estimate_duration_minutes(self, num_people: int) -> int:
        if num_people <= 2:
            return 90
        if num_people <= 6:
            return 120
        return 150

    def _normalize_preferred_zone(self, preferences: str) -> Optional[str]:
        pref = (preferences or "").lower()
        if "terraza" in pref:
            return "terraza"
        if "interior" in pref or "salon" in pref or "salón" in pref:
            return "interior"
        return None

    def _build_slot_keys(self, date: str, time: str, duration_min: int) -> List[str]:
        start = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        slots_needed = max(1, (duration_min + self.SLOT_MINUTES - 1) // self.SLOT_MINUTES)
        slots = []
        for offset in range(slots_needed):
            slot_dt = start + timedelta(minutes=offset * self.SLOT_MINUTES)
            slots.append(slot_dt.strftime("%Y-%m-%d#%H:%M"))
        return slots

    def _customer_item_key(self, phone: str, date: str, time: str) -> Tuple[str, str]:
        return f"CUSTOMER#{phone}", f"RESERVATION#{date}#{time}"

    def _reservation_is_active(self, status: str) -> bool:
        return status in {"pending", "confirmed"}

    def _ensure_table_catalog(self) -> None:
        try:
            for table_cfg in DEFAULT_TABLE_LAYOUT:
                pk = f"TABLE#{table_cfg['id']}"
                sk = "META"
                existing = self.table.get_item(Key={"PK": pk, "SK": sk})
                if "Item" in existing:
                    continue

                self.table.put_item(
                    Item={
                        "PK": pk,
                        "SK": sk,
                        "entity_type": "table",
                        "table_id": table_cfg["id"],
                        "zone": table_cfg["zone"],
                        "capacity_min": table_cfg["capacity_min"],
                        "capacity_max": table_cfg["capacity_max"],
                        "priority": table_cfg["priority"],
                        "is_active": table_cfg["is_active"],
                        "created_at": datetime.utcnow().isoformat(),
                    }
                )
            logger.info("✅ Catálogo de mesas inicializado")
        except ClientError as e:
            logger.error(f"❌ Error inicializando catálogo de mesas: {e.response['Error']['Message']}")

    def _load_active_tables(self) -> List[dict]:
        try:
            response = self.table.scan(
                FilterExpression=Attr("entity_type").eq("table") & Attr("is_active").eq(True)
            )
            items = response.get("Items", [])
            while "LastEvaluatedKey" in response:
                response = self.table.scan(
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                    FilterExpression=Attr("entity_type").eq("table") & Attr("is_active").eq(True),
                )
                items.extend(response.get("Items", []))

            tables = [self._dynamodb_to_python(item) for item in items]
            tables.sort(key=lambda x: (x.get("capacity_max", 999), x.get("priority", 999)))
            return tables
        except ClientError as e:
            logger.error(f"❌ Error cargando mesas: {e.response['Error']['Message']}")
            return []

    def _table_is_available(
        self, table_id: str, slot_keys: List[str], reservation_id: str, target_status: str
    ) -> bool:
        if not self._reservation_is_active(target_status):
            return True

        try:
            for slot_key in slot_keys:
                response = self.table.get_item(
                    Key={"PK": f"TABLE#{table_id}", "SK": f"SLOT#{slot_key}"}
                )
                item = response.get("Item")
                if not item:
                    continue

                status = item.get("status")
                owner = item.get("reservation_id")
                if owner == reservation_id:
                    continue
                if status in ("free", None):
                    continue
                return False
            return True
        except ClientError:
            return False

    def _choose_best_table(
        self,
        date: str,
        time: str,
        num_people: int,
        preferences: str,
        reservation_id: str,
        duration_min: int,
    ) -> Tuple[Optional[dict], List[str]]:
        preferred_zone = self._normalize_preferred_zone(preferences)
        slot_keys = self._build_slot_keys(date, time, duration_min)
        all_tables = self._load_active_tables()

        candidates = [
            t
            for t in all_tables
            if t.get("capacity_min", 1) <= num_people <= t.get("capacity_max", 999)
        ]

        if preferred_zone:
            preferred = [t for t in candidates if t.get("zone") == preferred_zone]
            if preferred:
                candidates = preferred

        for table in candidates:
            if self._table_is_available(
                table_id=table["table_id"],
                slot_keys=slot_keys,
                reservation_id=reservation_id,
                target_status="pending",
            ):
                return table, slot_keys

        return None, slot_keys

    def _build_reservation_item(self, reservation: dict) -> dict:
        return {
            "PK": f"RESERVATION#{reservation['id']}",
            "SK": "METADATA",
            "GSI1PK": f"DATE#{reservation['date']}",
            "GSI1SK": f"TIME#{reservation['time']}",
            **self._python_to_dynamodb(reservation),
        }

    def _build_customer_item(self, reservation: dict) -> dict:
        customer_pk, customer_sk = self._customer_item_key(
            reservation["phone"], reservation["date"], reservation["time"]
        )
        return {
            "PK": customer_pk,
            "SK": customer_sk,
            "reservation_id": reservation["id"],
            "date": reservation["date"],
            "time": reservation["time"],
            "status": reservation["status"],
        }

    def _build_slot_item(self, table_id: str, slot_key: str, reservation: dict) -> dict:
        return {
            "PK": f"TABLE#{table_id}",
            "SK": f"SLOT#{slot_key}",
            "entity_type": "slot",
            "date": reservation["date"],
            "time": slot_key.split("#")[1],
            "table_id": table_id,
            "status": "booked",
            "reservation_id": reservation["id"],
            "updated_at": datetime.utcnow().isoformat(),
        }

    def _put_slot_action(self, table_id: str, slot_key: str, reservation_id: str, reservation: dict) -> dict:
        return {
            "Put": {
                "TableName": settings.dynamodb_table_name,
                "Item": self._serialize_item(self._build_slot_item(table_id, slot_key, reservation)),
                # Permitimos crear el slot si no existe o sobreescribir solo si es del mismo reservation_id.
                # Evitamos referencias a claves/keywords en la condición para reducir ValidationError en transacciones.
                "ConditionExpression": "attribute_not_exists(reservation_id) OR reservation_id = :rid",
                "ExpressionAttributeValues": {
                    ":rid": self._serialize_value(reservation_id),
                },
            }
        }

    def _delete_slot_action(self, table_id: str, slot_key: str, reservation_id: str) -> dict:
        return {
            "Delete": {
                "TableName": settings.dynamodb_table_name,
                "Key": self._serialize_item({"PK": f"TABLE#{table_id}", "SK": f"SLOT#{slot_key}"}),
                "ConditionExpression": "attribute_not_exists(PK) OR reservation_id = :rid",
                "ExpressionAttributeValues": {":rid": self._serialize_value(reservation_id)},
            }
        }

    def _put_reservation_action(self, reservation: dict) -> dict:
        return {
            "Put": {
                "TableName": settings.dynamodb_table_name,
                "Item": self._serialize_item(self._build_reservation_item(reservation)),
            }
        }

    def _put_customer_action(self, reservation: dict) -> dict:
        return {
            "Put": {
                "TableName": settings.dynamodb_table_name,
                "Item": self._serialize_item(self._build_customer_item(reservation)),
            }
        }

    def _delete_customer_action(self, phone: str, date: str, time: str) -> dict:
        customer_pk, customer_sk = self._customer_item_key(phone, date, time)
        return {
            "Delete": {
                "TableName": settings.dynamodb_table_name,
                "Key": self._serialize_item({"PK": customer_pk, "SK": customer_sk}),
            }
        }

    def _transact_write(self, actions: List[dict]) -> None:
        try:
            self._ddb_client.transact_write_items(TransactItems=actions)
        except ClientError as e:
            reasons = e.response.get("CancellationReasons", [])
            if reasons:
                logger.error(f"❌ CancellationReasons: {reasons}")
            if actions:
                logger.error(f"❌ First TransactItem payload: {actions[0]}")
            raise

    def _prepare_reservation_defaults(self, reservation: dict) -> dict:
        now = datetime.utcnow().isoformat()
        normalized = dict(reservation)
        if "created_at" not in normalized:
            normalized["created_at"] = now
        normalized["updated_at"] = now

        duration = normalized.get("duration_min")
        if not duration:
            duration = self._estimate_duration_minutes(int(normalized["num_people"]))
        normalized["duration_min"] = duration
        normalized["slot_minutes"] = self.SLOT_MINUTES
        return normalized

    def put_reservation(self, reservation: dict) -> bool:
        """Crear o actualizar una reserva con asignación real de mesa/slots."""
        try:
            reservation_id = reservation["id"]
            current = self.get_reservation(reservation_id)

            if current:
                return self._update_reservation_with_slots(current, reservation)
            return self._create_reservation_with_slots(reservation)
        except Exception as e:
            logger.error(f"❌ Error guardando reserva: {e}")
            return False

    def _create_reservation_with_slots(self, reservation: dict) -> bool:
        reservation = self._prepare_reservation_defaults(reservation)
        reservation_id = reservation["id"]

        table, slot_keys = self._choose_best_table(
            date=reservation["date"],
            time=reservation["time"],
            num_people=int(reservation["num_people"]),
            preferences=reservation.get("preferences", ""),
            reservation_id=reservation_id,
            duration_min=int(reservation["duration_min"]),
        )

        if not table:
            logger.warning(f"⚠️ No hay mesas disponibles para {reservation['date']} {reservation['time']}")
            return False

        reservation["table_id"] = table["table_id"]
        reservation["table_zone"] = table.get("zone")

        actions: List[dict] = []
        if self._reservation_is_active(reservation.get("status", "pending")):
            for slot_key in slot_keys:
                actions.append(self._put_slot_action(table["table_id"], slot_key, reservation_id, reservation))

        actions.append(self._put_reservation_action(reservation))
        actions.append(self._put_customer_action(reservation))

        self._transact_write(actions)
        logger.info(f"✅ Reserva guardada: {reservation_id} | Mesa: {reservation['table_id']}")
        return True

    def _update_reservation_with_slots(self, current: dict, updates: dict) -> bool:
        merged = {**current, **updates}
        merged = self._prepare_reservation_defaults(merged)
        merged["created_at"] = current.get("created_at", merged["created_at"])

        current_status = current.get("status", "pending")
        target_status = merged.get("status", "pending")
        current_active = self._reservation_is_active(current_status)
        target_active = self._reservation_is_active(target_status)
        reservation_id = merged["id"]

        key_fields_changed = any(
            current.get(field) != merged.get(field)
            for field in ["date", "time", "num_people", "preferences"]
        )
        status_reactivated = (not current_active) and target_active
        needs_reallocation = key_fields_changed or status_reactivated or not merged.get("table_id")

        table = None
        slot_keys: List[str] = self._build_slot_keys(
            merged["date"], merged["time"], int(merged["duration_min"])
        )
        if target_active and needs_reallocation:
            table, slot_keys = self._choose_best_table(
                date=merged["date"],
                time=merged["time"],
                num_people=int(merged["num_people"]),
                preferences=merged.get("preferences", ""),
                reservation_id=reservation_id,
                duration_min=int(merged["duration_min"]),
            )
            if not table:
                logger.warning(f"⚠️ No hay mesas disponibles para reprogramar {reservation_id}")
                return False
            merged["table_id"] = table["table_id"]
            merged["table_zone"] = table.get("zone")

        actions: List[dict] = []
        old_slot_keys = self._build_slot_keys(
            current["date"], current["time"], int(current.get("duration_min", 120))
        )
        old_table_id = current.get("table_id")

        if target_active:
            target_table_id = merged.get("table_id")
            for slot_key in slot_keys:
                actions.append(self._put_slot_action(target_table_id, slot_key, reservation_id, merged))

        if current_active:
            should_release_old = (not target_active) or needs_reallocation
            if should_release_old and old_table_id:
                new_slot_set = set(slot_keys) if target_active else set()
                for slot_key in old_slot_keys:
                    if target_active and old_table_id == merged.get("table_id") and slot_key in new_slot_set:
                        continue
                    actions.append(self._delete_slot_action(old_table_id, slot_key, reservation_id))

        actions.append(self._put_reservation_action(merged))
        actions.append(self._put_customer_action(merged))

        old_customer_pk, old_customer_sk = self._customer_item_key(
            current["phone"], current["date"], current["time"]
        )
        new_customer_pk, new_customer_sk = self._customer_item_key(
            merged["phone"], merged["date"], merged["time"]
        )
        if old_customer_pk != new_customer_pk or old_customer_sk != new_customer_sk:
            actions.append(self._delete_customer_action(current["phone"], current["date"], current["time"]))

        self._transact_write(actions)
        logger.info(
            f"✅ Reserva actualizada: {reservation_id} | Mesa: {merged.get('table_id', 'N/A')} | Estado: {target_status}"
        )
        return True

    def get_reservation(self, reservation_id: str) -> Optional[dict]:
        """Obtener una reserva por ID."""
        try:
            response = self.table.get_item(
                Key={
                    "PK": f"RESERVATION#{reservation_id}",
                    "SK": "METADATA",
                }
            )

            if "Item" in response:
                item = self._dynamodb_to_python(response["Item"])
                return self._strip_internal_fields(item)

            return None

        except ClientError as e:
            logger.error(f"❌ Error obteniendo reserva: {e.response['Error']['Message']}")
            return None

    def query_reservations_by_date(self, date: str) -> List[dict]:
        """Obtener todas las reservas de una fecha."""
        try:
            response = self.table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("GSI1PK").eq(f"DATE#{date}"),
            )

            items = [self._dynamodb_to_python(item) for item in response.get("Items", [])]
            return [self._strip_internal_fields(item) for item in items]

        except ClientError as e:
            logger.error(f"❌ Error consultando por fecha: {e.response['Error']['Message']}")
            return []

    def query_reservations_by_status(self, status: str, date: Optional[str] = None) -> List[dict]:
        """Obtener reservas por estado, opcionalmente filtradas por fecha."""
        try:
            if date:
                response = self.table.query(
                    IndexName="StatusDateIndex",
                    KeyConditionExpression=Key("status").eq(status) & Key("date").eq(date),
                )
            else:
                response = self.table.query(
                    IndexName="StatusDateIndex",
                    KeyConditionExpression=Key("status").eq(status),
                )

            items = [self._dynamodb_to_python(item) for item in response.get("Items", [])]
            reservations = []
            for item in items:
                if item.get("SK") == "METADATA":
                    reservations.append(self._strip_internal_fields(item))
            return reservations

        except ClientError as e:
            logger.error(f"❌ Error consultando por estado: {e.response['Error']['Message']}")
            return []

    def query_customer_reservations(self, phone: str) -> List[dict]:
        """Obtener todas las reservas de un cliente."""
        try:
            response = self.table.query(
                KeyConditionExpression=Key("PK").eq(f"CUSTOMER#{phone}")
            )

            reservation_ids = [
                item["reservation_id"]
                for item in response.get("Items", [])
                if "reservation_id" in item
            ]

            reservations = []
            for res_id in reservation_ids:
                reservation = self.get_reservation(res_id)
                if reservation:
                    reservations.append(reservation)

            return reservations

        except ClientError as e:
            logger.error(f"❌ Error consultando reservas de cliente: {e.response['Error']['Message']}")
            return []

    def update_reservation(self, reservation_id: str, updates: dict) -> bool:
        """Actualizar campos específicos de una reserva."""
        try:
            current = self.get_reservation(reservation_id)
            if not current:
                return False

            updated_reservation = {**current, **updates}
            updated_reservation["updated_at"] = datetime.utcnow().isoformat()
            return self.put_reservation(updated_reservation)
        except Exception as e:
            logger.error(f"❌ Error actualizando reserva: {e}")
            return False

    def delete_reservation(self, reservation_id: str) -> bool:
        """Eliminar una reserva (soft delete: cambiar status a cancelled)."""
        try:
            return self.update_reservation(reservation_id, {"status": "cancelled"})
        except Exception as e:
            logger.error(f"❌ Error eliminando reserva: {e}")
            return False

    def scan_all_reservations(self, status_filter: Optional[str] = None) -> List[dict]:
        """
        Escanear todas las reservas (usar con cuidado, costoso).
        Solo para admin/stats.
        """
        try:
            scan_kwargs = {
                "FilterExpression": Attr("SK").eq("METADATA")
            }

            if status_filter:
                scan_kwargs["FilterExpression"] = (
                    Attr("SK").eq("METADATA") & Attr("status").eq(status_filter)
                )

            response = self.table.scan(**scan_kwargs)
            items = response.get("Items", [])

            while "LastEvaluatedKey" in response:
                response = self.table.scan(
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                    **scan_kwargs,
                )
                items.extend(response.get("Items", []))

            reservations = []
            for item in items:
                item = self._dynamodb_to_python(item)
                reservations.append(self._strip_internal_fields(item))

            return reservations

        except ClientError as e:
            logger.error(f"❌ Error escaneando reservas: {e.response['Error']['Message']}")
            return []


db_client = DynamoDBClient()
