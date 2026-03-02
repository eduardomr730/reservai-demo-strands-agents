"""
Tools de Strands para gestión de reservas.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from strands import tool

from app.reservations.runtime import get_reservation_service
from app.reservations.service import (
    ConfigurationError,
    NoAvailabilityError,
    ReservationConflictError,
    ReservationError,
    ReservationNotFoundError,
)

logger = logging.getLogger(__name__)


@lru_cache()
def _service():
    return get_reservation_service()


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "content": [{"text": json.dumps(payload, ensure_ascii=False)}],
    }


def _error(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error_code": code,
        "message": message,
    }
    if details:
        payload["details"] = details
    return {
        "status": "error",
        "content": [{"text": json.dumps(payload, ensure_ascii=False)}],
    }


@tool
def check_availability(date: str, party_size: int, preferred_time: str = "") -> dict[str, Any]:
    """
    Consulta disponibilidad de mesas para una fecha y número de personas.

    Args:
        date: Fecha en formato YYYY-MM-DD (zona Europe/Madrid).
        party_size: Número de personas.
        preferred_time: Hora opcional en formato HH:MM para filtrar una franja concreta.

    Returns:
        JSON con franjas horarias disponibles y número de mesas libres.
    """
    try:
        result = _service().check_availability(
            service_date=date,
            party_size=int(party_size),
            preferred_time=preferred_time or None,
        )
        result["ok"] = True
        return _ok(result)
    except (ReservationError, NoAvailabilityError, ConfigurationError) as exc:
        return _error("availability_error", str(exc))
    except Exception as exc:
        logger.exception("Error inesperado en check_availability")
        return _error("internal_error", "No se pudo consultar disponibilidad.", details={"reason": str(exc)})


@tool
def create_reservation(
    phone: str,
    customer_name: str,
    date: str,
    time: str,
    party_size: int,
    notes: str = "",
) -> dict[str, Any]:
    """
    Crea una reserva bloqueando los slots consecutivos necesarios para su duración.

    Args:
        phone: Teléfono del cliente (usar teléfono de metadata WhatsApp).
        customer_name: Nombre del cliente.
        date: Fecha de la reserva en YYYY-MM-DD.
        time: Hora exacta de la reserva en HH:MM.
        party_size: Número de personas.
        notes: Preferencias o comentarios opcionales.

    Returns:
        JSON con la reserva creada.
    """
    try:
        result = _service().create_reservation(
            phone=phone,
            customer_name=customer_name,
            service_date=date,
            service_time=time,
            party_size=int(party_size),
            notes=notes,
        )
        result["ok"] = True
        return _ok(result)
    except NoAvailabilityError as exc:
        return _error("no_availability", str(exc))
    except ReservationConflictError as exc:
        return _error("reservation_conflict", str(exc))
    except (ReservationError, ConfigurationError) as exc:
        return _error("reservation_error", str(exc))
    except Exception as exc:
        logger.exception("Error inesperado en create_reservation")
        return _error("internal_error", "No se pudo crear la reserva.", details={"reason": str(exc)})


@tool
def cancel_reservation(phone: str, reservation_id: str = "", date: str = "", time: str = "") -> dict[str, Any]:
    """
    Cancela una reserva activa y libera su slot de nuevo.

    Args:
        phone: Teléfono del cliente (usar teléfono de metadata WhatsApp).
        reservation_id: ID de reserva (opcional, recomendado si existe).
        date: Fecha en YYYY-MM-DD para localizar la reserva si no hay ID.
        time: Hora en HH:MM para localizar la reserva si no hay ID.

    Returns:
        JSON con el resultado de cancelación.
    """
    try:
        result = _service().cancel_reservation(
            phone=phone,
            reservation_id=reservation_id or None,
            service_date=date or None,
            service_time=time or None,
        )
        result["ok"] = True
        return _ok(result)
    except ReservationNotFoundError as exc:
        return _error("reservation_not_found", str(exc))
    except ReservationConflictError as exc:
        return _error("reservation_conflict", str(exc))
    except ReservationError as exc:
        return _error("reservation_error", str(exc))
    except Exception as exc:
        logger.exception("Error inesperado en cancel_reservation")
        return _error("internal_error", "No se pudo cancelar la reserva.", details={"reason": str(exc)})


@tool
def list_reservations(phone: str, only_active: bool = True, limit: int = 5) -> dict[str, Any]:
    """
    Lista reservas de un teléfono para consultar estado o elegir cuál cancelar.

    Args:
        phone: Teléfono del cliente.
        only_active: Si true, devuelve solo reservas activas.
        limit: Límite de resultados (1-25).

    Returns:
        JSON con reservas encontradas.
    """
    try:
        result = _service().list_reservations(
            phone=phone,
            only_active=bool(only_active),
            limit=int(limit),
        )
        result["ok"] = True
        return _ok(result)
    except ReservationError as exc:
        return _error("reservation_error", str(exc))
    except Exception as exc:
        logger.exception("Error inesperado en list_reservations")
        return _error("internal_error", "No se pudieron listar reservas.", details={"reason": str(exc)})


reservation_tools = [
    check_availability,
    create_reservation,
    cancel_reservation,
    list_reservations,
]
