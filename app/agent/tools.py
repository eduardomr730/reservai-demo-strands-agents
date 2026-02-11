"""Agent tools for restaurant reservations."""

from __future__ import annotations

import json
from datetime import datetime

from strands import tool

from app.database.reservation_repository import VALID_STATUSES, reservation_repository


def _format_date_human(date: str) -> str:
    try:
        parsed = datetime.strptime(date, "%Y-%m-%d")
        days = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        return f"{days[parsed.weekday()]}, {parsed.strftime('%d/%m/%Y')}"
    except ValueError:
        return date


@tool
def create_reservation(
    date: str,
    time: str,
    num_people: int,
    customer_name: str,
    phone: str,
    special_occasion: str = "",
    preferences: str = "",
) -> str:
    """Create a restaurant reservation with real table and slot assignment."""
    if not customer_name.strip():
        return "❌ Debes indicar el nombre del cliente"

    if len(phone.strip()) < 9:
        return "❌ Debes indicar un teléfono válido"

    success, error, reservation = reservation_repository.create_reservation(
        {
            "date": date,
            "time": time,
            "num_people": int(num_people),
            "customer_name": customer_name.strip(),
            "phone": phone.strip(),
            "special_occasion": special_occasion.strip(),
            "preferences": preferences.strip(),
            "status": "pending",
        }
    )

    if not success or not reservation:
        return f"❌ {error}"

    details = [
        "✅ Reserva creada",
        f"ID: {reservation['id']}",
        f"Cliente: {reservation['customer_name']}",
        f"Fecha: {_format_date_human(reservation['date'])}",
        f"Hora: {reservation['time']}",
        f"Personas: {reservation['num_people']}",
        f"Mesa: {reservation['table_id']} ({reservation.get('table_zone', 'salon')})",
        "Estado: pending",
    ]

    if reservation.get("special_occasion"):
        details.append(f"Ocasión: {reservation['special_occasion']}")
    if reservation.get("preferences"):
        details.append(f"Preferencias: {reservation['preferences']}")

    return "\n".join(details)


@tool
def check_availability(date: str, num_people: int, preferred_zone: str = "") -> str:
    """Check available time slots for a given date and party size."""
    if num_people < 1 or num_people > 12:
        return "❌ El número de personas debe estar entre 1 y 12"

    available = reservation_repository.available_times(date, int(num_people), preferred_zone)
    if not available:
        return "No hay disponibilidad para esa fecha y tamaño de grupo"

    return json.dumps(
        {
            "date": date,
            "num_people": num_people,
            "preferred_zone": preferred_zone or "sin preferencia",
            "available": available,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
def list_reservations(date: str = "", status: str = "all", customer_name: str = "") -> str:
    """List reservations with optional filters."""
    status = status.lower().strip() or "all"
    if status != "all" and status not in VALID_STATUSES:
        return "❌ Estado inválido. Usa: all, pending, confirmed, cancelled"

    reservations = reservation_repository.list_reservations(
        date=date.strip(),
        status=status,
        customer_name=customer_name.strip(),
    )

    if not reservations:
        return "No se encontraron reservas con esos filtros"

    return json.dumps(
        {
            "total": len(reservations),
            "filters": {
                "date": date or "all",
                "status": status,
                "customer_name": customer_name or "all",
            },
            "reservations": reservations,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
def update_reservation(
    reservation_id: str,
    new_date: str = "",
    new_time: str = "",
    new_num_people: int = 0,
    new_phone: str = "",
    new_special_occasion: str = "",
    new_preferences: str = "",
    status: str = "",
) -> str:
    """Update reservation fields and recalculate table assignment when needed."""
    updates: dict[str, object] = {}

    if new_date:
        updates["date"] = new_date.strip()
    if new_time:
        updates["time"] = new_time.strip()
    if new_num_people > 0:
        updates["num_people"] = int(new_num_people)
    if new_phone:
        if len(new_phone.strip()) < 9:
            return "❌ Número de teléfono inválido"
        updates["phone"] = new_phone.strip()
    if new_special_occasion:
        updates["special_occasion"] = new_special_occasion.strip()
    if new_preferences:
        updates["preferences"] = new_preferences.strip()
    if status:
        normalized = status.lower().strip()
        if normalized not in VALID_STATUSES:
            return "❌ Estado inválido. Usa pending, confirmed o cancelled"
        updates["status"] = normalized

    if not updates:
        return "⚠️ No se recibieron cambios"

    success, error, reservation = reservation_repository.update_reservation(reservation_id.strip(), updates)
    if not success or not reservation:
        return f"❌ {error}"

    return "\n".join(
        [
            "✅ Reserva actualizada",
            f"ID: {reservation['id']}",
            f"Fecha: {_format_date_human(reservation['date'])}",
            f"Hora: {reservation['time']}",
            f"Personas: {reservation['num_people']}",
            f"Mesa: {reservation.get('table_id', 'sin asignar')} ({reservation.get('table_zone', 'N/A')})",
            f"Estado: {reservation['status']}",
        ]
    )


@tool
def cancel_reservation(reservation_id: str, reason: str = "") -> str:
    """Cancel a reservation and release its table slots."""
    updates = {"status": "cancelled"}
    if reason.strip():
        updates["preferences"] = f"{reason.strip()}"

    success, error, reservation = reservation_repository.update_reservation(reservation_id.strip(), updates)
    if not success or not reservation:
        return f"❌ {error}"

    return "\n".join(
        [
            "❌ Reserva cancelada",
            f"ID: {reservation['id']}",
            f"Cliente: {reservation['customer_name']}",
            f"Fecha: {_format_date_human(reservation['date'])}",
            f"Hora: {reservation['time']}",
        ]
    )


@tool
def get_reservation_details(reservation_id: str) -> str:
    """Get full reservation details by reservation id."""
    reservation = reservation_repository.get_reservation(reservation_id.strip())
    if not reservation:
        return "❌ No se encontró la reserva"

    return json.dumps(
        {
            "id": reservation.get("id"),
            "customer_name": reservation.get("customer_name"),
            "phone": reservation.get("phone"),
            "date": reservation.get("date"),
            "formatted_date": _format_date_human(str(reservation.get("date", ""))),
            "time": reservation.get("time"),
            "num_people": reservation.get("num_people"),
            "table_id": reservation.get("table_id"),
            "table_zone": reservation.get("table_zone"),
            "status": reservation.get("status"),
            "special_occasion": reservation.get("special_occasion", ""),
            "preferences": reservation.get("preferences", ""),
            "created_at": reservation.get("created_at"),
            "updated_at": reservation.get("updated_at"),
        },
        ensure_ascii=False,
        indent=2,
    )
