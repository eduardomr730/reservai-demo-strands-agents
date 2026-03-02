"""
Inicialización lazy del servicio de reservas.
"""
from functools import lru_cache

from app.config import settings
from app.reservations.service import DynamoReservationRepository, ReservationService


@lru_cache()
def get_reservation_service() -> ReservationService:
    repository = DynamoReservationRepository(
        table_name=settings.dynamodb_table_name,
        region_name=settings.dynamodb_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        aws_session_token=settings.aws_session_token,
    )
    return ReservationService(
        repository=repository,
        timezone_name=settings.reservation_timezone,
        slot_minutes=settings.reservation_slot_minutes,
        reservation_duration_minutes=settings.reservation_duration_minutes,
        auto_seed_tables=settings.reservation_auto_seed_tables,
        auto_publish_slots=settings.reservation_auto_publish_slots,
    )
