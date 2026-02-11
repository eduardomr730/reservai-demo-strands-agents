"""
Herramientas (tools) para el agente - usando DynamoDB.
"""
import uuid
import json
from datetime import datetime
from typing import Optional
from strands import tool
from app.database.dynamodb_client import db_client
from app.config import settings


@tool
def create_reservation(
    date: str,
    time: str,
    num_people: int,
    customer_name: str,
    phone: str,
    special_occasion: str = "",
    preferences: str = ""
) -> str:
    """
    Create a new restaurant reservation in DynamoDB.
    
    Args:
        date (str): Date of the reservation (format: YYYY-MM-DD).
        time (str): Time of the reservation (format: HH:MM, must be within restaurant hours).
        num_people (int): Number of people for the reservation (1-20).
        customer_name (str): Full name of the customer making the reservation.
        phone (str): Contact phone number.
        special_occasion (str, optional): Special occasion (birthday, anniversary, etc.).
        preferences (str, optional): Special preferences (terrace, window table, allergies, etc.).

    Returns:
        str: Confirmation message with the reservation ID and details.

    Raises:
        ValueError: If date/time format is invalid or restaurant is closed.
        ValueError: If number of people exceeds capacity or is invalid.
    """
    # Validar fecha
    try:
        reservation_date = datetime.strptime(date, "%Y-%m-%d")
        day_of_week = reservation_date.weekday()
    except ValueError:
        raise ValueError("Date must be in format 'YYYY-MM-DD'")
    
    # Validar hora
    try:
        reservation_time = datetime.strptime(time, "%H:%M")
        hour = reservation_time.hour
        minute = reservation_time.minute
    except ValueError:
        raise ValueError("Time must be in format 'HH:MM'")

    if minute not in (0, 30):
        raise ValueError("Las reservas se gestionan en bloques de 30 minutos (ej: 20:00, 20:30, 21:00)")
    
    # Check if restaurant is closed (Monday = 0)
    if day_of_week == 0:
        raise ValueError("Lo sentimos, el restaurante está cerrado los lunes. Por favor elige otro día.")
    
    # Validate restaurant hours based on day
    if day_of_week in [1, 2, 3, 4]:  # Tuesday to Friday
        valid_lunch = (13 <= hour < 16) or (hour == 16 and minute == 0)
        valid_dinner = (20 <= hour < 23) or (hour == 23 and minute <= 30)
        if not (valid_lunch or valid_dinner):
            raise ValueError("Horario no disponible. Martes a Viernes: 13:00-16:00 y 20:00-23:30")
    elif day_of_week == 5:  # Saturday
        if not (13 <= hour < 24 or (hour == 0 and minute == 0)):
            raise ValueError("Horario no disponible. Sábados: 13:00-00:00")
    elif day_of_week == 6:  # Sunday
        if not (13 <= hour < 18):
            raise ValueError("Horario no disponible. Domingos: 13:00-18:00")
    
    # Validate number of people
    if not isinstance(num_people, int) or num_people < 1:
        raise ValueError("El número de personas debe ser al menos 1")
    if num_people > 20:
        raise ValueError("Para grupos mayores a 20 personas, por favor contacta directamente al restaurante")
    
    # Validate phone
    if not phone or len(phone) < 9:
        raise ValueError("Por favor proporciona un número de teléfono válido")
    
    # Generate reservation ID
    date_part = reservation_date.strftime("%Y%m%d")
    unique_part = str(uuid.uuid4())[:8].upper()
    reservation_id = f"RES-{date_part}-{unique_part}"
    
    # Create reservation object
    reservation = {
        'id': reservation_id,
        'date': date,
        'time': time,
        'num_people': num_people,
        'customer_name': customer_name,
        'phone': phone,
        'special_occasion': special_occasion,
        'preferences': preferences,
        'status': 'pending'
    }
    
    # Save to DynamoDB
    success = db_client.put_reservation(reservation)
    
    if not success:
        raise Exception("Error al guardar la reserva. Por favor intenta de nuevo.")

    saved_reservation = db_client.get_reservation(reservation_id) or {}
    table_text = ""
    if saved_reservation.get("table_id"):
        zone = saved_reservation.get("table_zone", "salón")
        table_text = f"\n🪑 Mesa asignada: {saved_reservation['table_id']} ({zone})"
    
    # Format confirmation message
    occasion_text = f"\n🎉 Ocasión especial: {special_occasion}" if special_occasion else ""
    preferences_text = f"\n📝 Preferencias: {preferences}" if preferences else ""
    
    confirmation_message = f"""
✅ ¡Reserva registrada exitosamente!

📋 ID de Reserva: {reservation_id}
👤 Nombre: {customer_name}
📅 Fecha: {date}
🕐 Hora: {time}
👥 Número de personas: {num_people}
📞 Teléfono: {phone}{table_text}{occasion_text}{preferences_text}

⏳ Estado: Pendiente de confirmación

Nuestro equipo te confirmará la reserva por WhatsApp en las próximas 2 horas.

¡Nos vemos pronto en El Rincón de Andalucía! 🇪🇸✨
    """
    
    return confirmation_message.strip()


@tool
def list_reservations(date: str = "", status: str = "all", customer_name: str = "") -> str:
    """
    List all restaurant reservations from DynamoDB with optional filters.
    
    Args:
        date (str, optional): Filter by specific date (format: YYYY-MM-DD). Leave empty for all dates.
        status (str, optional): Filter by status ('pending', 'confirmed', 'cancelled', 'all'). Default: 'all'.
        customer_name (str, optional): Filter by customer name (partial match). Leave empty for all customers.
    
    Returns:
        str: JSON string with the list of reservations or a message if none found.
    """
    try:
        reservations = []
        
        # Si hay filtro de fecha, usar query optimizado
        if date:
            try:
                datetime.strptime(date, "%Y-%m-%d")
                reservations = db_client.query_reservations_by_date(date)
            except ValueError:
                return "Formato de fecha inválido. Usa YYYY-MM-DD"
        
        # Si hay filtro de estado pero no de fecha
        elif status.lower() != "all":
            if status.lower() in ['pending', 'confirmed', 'cancelled']:
                reservations = db_client.query_reservations_by_status(status.lower())
            else:
                return "Estado inválido. Usa: 'pending', 'confirmed', 'cancelled' o 'all'"
        
        # Si hay filtro de nombre de cliente (scan completo)
        elif customer_name:
            all_reservations = db_client.scan_all_reservations()
            reservations = [
                r for r in all_reservations 
                if customer_name.lower() in r.get('customer_name', '').lower()
            ]
        
        # Sin filtros: scan completo
        else:
            reservations = db_client.scan_all_reservations()
        
        # Filtrar por status si es necesario (para queries que no lo hicieron)
        if status.lower() != "all" and date:
            reservations = [r for r in reservations if r.get('status') == status.lower()]
        
        if not reservations:
            filter_msg = []
            if date:
                filter_msg.append(f"fecha {date}")
            if status != "all":
                filter_msg.append(f"estado '{status}'")
            if customer_name:
                filter_msg.append(f"cliente '{customer_name}'")
            
            if filter_msg:
                return f"No se encontraron reservas con los filtros: {', '.join(filter_msg)}"
            return "No hay reservas registradas en el sistema"
        
        # Formatear reservas
        formatted_reservations = []
        for row in reservations:
            try:
                date_obj = datetime.strptime(row['date'], "%Y-%m-%d")
                formatted_date = date_obj.strftime("%d/%m/%Y")
                day_name = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][date_obj.weekday()]
                full_date = f"{day_name}, {formatted_date}"
            except:
                full_date = row['date']
            
            status_emoji = {
                'pending': '⏳',
                'confirmed': '✅',
                'cancelled': '❌'
            }.get(row.get('status'), '📋')
            
            reservation = {
                'id': row['id'],
                'date': row['date'],
                'formatted_date': full_date,
                'time': row['time'],
                'num_people': row['num_people'],
                'customer_name': row['customer_name'],
                'phone': row['phone'],
                'table_id': row.get('table_id', ''),
                'table_zone': row.get('table_zone', ''),
                'special_occasion': row.get('special_occasion', ''),
                'preferences': row.get('preferences', ''),
                'status': row['status'],
                'status_display': f"{status_emoji} {row['status'].capitalize()}",
                'created_at': row.get('created_at', '')
            }
            formatted_reservations.append(reservation)
        
        summary = {
            'total_reservations': len(formatted_reservations),
            'filters_applied': {
                'date': date if date else 'todas las fechas',
                'status': status,
                'customer_name': customer_name if customer_name else 'todos los clientes'
            },
            'reservations': formatted_reservations
        }
        
        return json.dumps(summary, ensure_ascii=False, indent=2)
    
    except Exception as e:
        return f"Error al consultar las reservas: {str(e)}"


@tool
def update_reservation(
    reservation_id: str,
    new_date: str = "",
    new_time: str = "",
    new_num_people: int = 0,
    new_phone: str = "",
    new_special_occasion: str = "",
    new_preferences: str = "",
    status: str = ""
) -> str:
    """
    Update an existing restaurant reservation or change its status.
    
    Args:
        reservation_id (str): The unique reservation ID (format: RES-YYYYMMDD-XXXX).
        new_date (str, optional): New date for the reservation (format: YYYY-MM-DD).
        new_time (str, optional): New time for the reservation (format: HH:MM).
        new_num_people (int, optional): New number of people (1-20).
        new_phone (str, optional): New contact phone number.
        new_special_occasion (str, optional): Update special occasion.
        new_preferences (str, optional): Update special preferences.
        status (str, optional): Update status ('pending', 'confirmed', 'cancelled').
    
    Returns:
        str: Confirmation message with updated reservation details.
    
    Raises:
        ValueError: If reservation ID doesn't exist or validation fails.
    """
    # Obtener reserva actual
    existing_reservation = db_client.get_reservation(reservation_id)
    
    if not existing_reservation:
        return f"❌ No se encontró la reserva con ID: {reservation_id}"
    
    updates = {}
    changes_made = []
    
    # ============================================
    # VALIDAR Y PREPARAR NUEVA FECHA
    # ============================================
    if new_date:
        try:
            reservation_date = datetime.strptime(new_date, "%Y-%m-%d")
            day_of_week = reservation_date.weekday()
            
            # Check if restaurant is closed (Monday = 0)
            if day_of_week == 0:
                return "❌ El restaurante está cerrado los lunes. Por favor elige otro día."
            
            updates['date'] = new_date
            changes_made.append(f"📅 Fecha: {existing_reservation['date']} → {new_date}")
        except ValueError:
            return "❌ Formato de fecha inválido. Usa YYYY-MM-DD"
    
    # ============================================
    # VALIDAR Y PREPARAR NUEVA HORA
    # ============================================
    if new_time:
        try:
            reservation_time = datetime.strptime(new_time, "%H:%M")
            hour = reservation_time.hour
            minute = reservation_time.minute
            if minute not in (0, 30):
                return "❌ Las reservas se gestionan en bloques de 30 minutos (ej: 20:00, 20:30, 21:00)"
            
            # Usar nueva fecha si existe, sino usar la existente
            check_date = new_date if new_date else existing_reservation['date']
            day_of_week = datetime.strptime(check_date, "%Y-%m-%d").weekday()
            
            # Validar horario según día
            valid_time = False
            error_msg = ""
            
            if day_of_week in [1, 2, 3, 4]:  # Tuesday to Friday
                valid_lunch = (13 <= hour < 16) or (hour == 16 and minute == 0)
                valid_dinner = (20 <= hour < 23) or (hour == 23 and minute <= 30)
                valid_time = valid_lunch or valid_dinner
                error_msg = "Horario no disponible. Martes a Viernes: 13:00-16:00 y 20:00-23:30"
            elif day_of_week == 5:  # Saturday
                valid_time = (13 <= hour < 24) or (hour == 0 and minute == 0)
                error_msg = "Horario no disponible. Sábados: 13:00-00:00"
            elif day_of_week == 6:  # Sunday
                valid_time = (13 <= hour < 18)
                error_msg = "Horario no disponible. Domingos: 13:00-18:00"
            
            if not valid_time:
                return f"❌ {error_msg}"
            
            updates['time'] = new_time
            changes_made.append(f"🕐 Hora: {existing_reservation['time']} → {new_time}")
        except ValueError:
            return "❌ Formato de hora inválido. Usa HH:MM"
    
    # ============================================
    # VALIDAR Y PREPARAR NUEVO NÚMERO DE PERSONAS
    # ============================================
    if new_num_people > 0:
        if new_num_people < 1:
            return "❌ El número de personas debe ser al menos 1"
        if new_num_people > 20:
            return "❌ Para grupos mayores a 20 personas, contacta directamente al restaurante"
        
        updates['num_people'] = new_num_people
        changes_made.append(f"👥 Personas: {existing_reservation['num_people']} → {new_num_people}")
    
    # ============================================
    # VALIDAR Y PREPARAR NUEVO TELÉFONO
    # ============================================
    if new_phone:
        if len(new_phone) < 9:
            return "❌ Número de teléfono inválido"
        
        updates['phone'] = new_phone
        changes_made.append(f"📞 Teléfono: {existing_reservation['phone']} → {new_phone}")
    
    # ============================================
    # PREPARAR NUEVA OCASIÓN ESPECIAL
    # ============================================
    if new_special_occasion:
        updates['special_occasion'] = new_special_occasion
        old_occasion = existing_reservation.get('special_occasion', '') or 'Ninguna'
        changes_made.append(f"🎉 Ocasión especial: {old_occasion} → {new_special_occasion}")
    
    # ============================================
    # PREPARAR NUEVAS PREFERENCIAS
    # ============================================
    if new_preferences:
        updates['preferences'] = new_preferences
        old_prefs = existing_reservation.get('preferences', '') or 'Ninguna'
        changes_made.append(f"📝 Preferencias: {old_prefs} → {new_preferences}")
    
    # ============================================
    # VALIDAR Y PREPARAR NUEVO ESTADO
    # ============================================
    if status:
        status_lower = status.lower()
        if status_lower not in ['pending', 'confirmed', 'cancelled']:
            return "❌ Estado inválido. Usa: 'pending', 'confirmed' o 'cancelled'"
        
        updates['status'] = status_lower
        
        status_emoji_map = {
            'pending': '⏳ Pendiente',
            'confirmed': '✅ Confirmada',
            'cancelled': '❌ Cancelada'
        }
        
        old_status = status_emoji_map.get(existing_reservation['status'], existing_reservation['status'])
        new_status_display = status_emoji_map.get(status_lower, status_lower)
        changes_made.append(f"📊 Estado: {old_status} → {new_status_display}")
    
    # ============================================
    # VERIFICAR QUE HAY CAMBIOS
    # ============================================
    if not updates:
        return "⚠️ No se especificaron cambios para realizar"
    
    # ============================================
    # EJECUTAR UPDATE EN DYNAMODB
    # ============================================
    success = db_client.update_reservation(reservation_id, updates)
    
    if not success:
        return "❌ Error al actualizar la reserva en la base de datos"
    
    # ============================================
    # OBTENER RESERVA ACTUALIZADA
    # ============================================
    updated_reservation = db_client.get_reservation(reservation_id)
    
    if not updated_reservation:
        return "❌ Error al recuperar la reserva actualizada"
    
    # ============================================
    # FORMATEAR MENSAJE DE CONFIRMACIÓN
    # ============================================
    
    # Formatear fecha legible
    try:
        date_obj = datetime.strptime(updated_reservation['date'], "%Y-%m-%d")
        formatted_date = date_obj.strftime("%d/%m/%Y")
        day_name = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][date_obj.weekday()]
        full_date = f"{day_name}, {formatted_date}"
    except:
        full_date = updated_reservation['date']
    
    # Status display con emoji
    status_emoji_map = {
        'pending': '⏳',
        'confirmed': '✅',
        'cancelled': '❌'
    }
    status_emoji = status_emoji_map.get(updated_reservation['status'], '📋')
    status_display = f"{status_emoji} {updated_reservation['status'].capitalize()}"
    
    # Lista de cambios realizados
    changes_text = "\n".join([f"  • {change}" for change in changes_made])
    
    # Ocasión especial y preferencias (si existen)
    occasion_text = ""
    if updated_reservation.get('special_occasion'):
        occasion_text = f"\n🎉 Ocasión especial: {updated_reservation['special_occasion']}"

    table_text = ""
    if updated_reservation.get('table_id'):
        table_text = f"\n🪑 Mesa asignada: {updated_reservation['table_id']} ({updated_reservation.get('table_zone', 'salón')})"

    preferences_text = ""
    if updated_reservation.get('preferences'):
        preferences_text = f"\n📝 Preferencias: {updated_reservation['preferences']}"
    
    # Mensaje final según estado
    final_message = ""
    if updated_reservation['status'] == 'cancelled':
        final_message = "\n\n⚠️ Esta reserva ha sido CANCELADA"
    elif updated_reservation['status'] == 'confirmed':
        final_message = "\n\n¡Te esperamos en El Rincón de Andalucía! 🇪🇸✨"
    else:
        final_message = "\n\n⏳ Reserva pendiente de confirmación"
    
    # ============================================
    # CONSTRUIR MENSAJE COMPLETO
    # ============================================
    confirmation_message = f"""
✅ ¡Reserva actualizada exitosamente!

📋 ID de Reserva: {reservation_id}

🔄 CAMBIOS REALIZADOS:
{changes_text}

📌 DETALLES ACTUALIZADOS:
👤 Nombre: {updated_reservation['customer_name']}
📅 Fecha: {full_date}
🕐 Hora: {updated_reservation['time']}
👥 Número de personas: {updated_reservation['num_people']}
📞 Teléfono: {updated_reservation['phone']}{table_text}{occasion_text}{preferences_text}
📊 Estado: {status_display}{final_message}
    """
    
    return confirmation_message.strip()


@tool
def cancel_reservation(reservation_id: str, reason: str = "") -> str:
    """
    Cancel an existing reservation (shortcut for updating status to cancelled).
    
    Args:
        reservation_id (str): The unique reservation ID to cancel.
        reason (str, optional): Reason for cancellation.
    
    Returns:
        str: Cancellation confirmation message.
    """
    # Obtener reserva actual
    reservation = db_client.get_reservation(reservation_id)
    
    if not reservation:
        return f"❌ No se encontró la reserva con ID: {reservation_id}"
    
    # Verificar si ya está cancelada
    if reservation['status'] == 'cancelled':
        return "⚠️ Esta reserva ya estaba cancelada previamente"
    
    # Preparar updates
    updates = {'status': 'cancelled'}
    
    # Añadir razón a preferencias si existe
    if reason:
        current_prefs = reservation.get('preferences', '')
        separator = " | " if current_prefs else ""
        updates['preferences'] = f"{current_prefs}{separator}Motivo cancelación: {reason}"
    
    # Ejecutar update
    success = db_client.update_reservation(reservation_id, updates)
    
    if not success:
        return "❌ Error al cancelar la reserva"
    
    # Formatear fecha legible
    try:
        date_obj = datetime.strptime(reservation['date'], "%Y-%m-%d")
        formatted_date = date_obj.strftime("%d/%m/%Y")
        day_name = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][date_obj.weekday()]
        full_date = f"{day_name}, {formatted_date}"
    except:
        full_date = reservation['date']
    
    # Texto de razón (si existe)
    reason_text = f"\n💬 Motivo: {reason}" if reason else ""
    
    # ============================================
    # CONSTRUIR MENSAJE DE CANCELACIÓN
    # ============================================
    cancellation_message = f"""
❌ Reserva cancelada exitosamente

📋 ID de Reserva: {reservation_id}
👤 Cliente: {reservation['customer_name']}
📅 Fecha: {full_date}
🕐 Hora: {reservation['time']}
👥 Personas: {reservation['num_people']}{reason_text}

La reserva ha sido cancelada. Si deseas realizar una nueva reserva, estaremos encantados de atenderte.

Política de cancelación: Sin cargo por cancelación con más de 12 horas de anticipación.

¡Esperamos verte pronto en El Rincón de Andalucía! 🇪🇸
    """
    
    return cancellation_message.strip()


@tool
def get_reservation_details(reservation_id: str) -> str:
    """
    Get detailed information about a specific reservation.
    
    Args:
        reservation_id (str): The unique reservation ID.
    
    Returns:
        str: Detailed reservation information or error message.
    """
    # Obtener reserva
    reservation = db_client.get_reservation(reservation_id)
    
    if not reservation:
        return f"❌ No se encontró la reserva con ID: {reservation_id}"
    
    # Formatear fecha legible
    try:
        date_obj = datetime.strptime(reservation['date'], "%Y-%m-%d")
        formatted_date = date_obj.strftime("%d/%m/%Y")
        day_name = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][date_obj.weekday()]
        full_date = f"{day_name}, {formatted_date}"
    except:
        full_date = reservation['date']
    
    # Status display con emoji
    status_emoji_map = {
        'pending': '⏳ Pendiente',
        'confirmed': '✅ Confirmada',
        'cancelled': '❌ Cancelada'
    }
    status_display = status_emoji_map.get(reservation['status'], reservation['status'])
    
    # Ocasión especial (si existe)
    occasion_text = ""
    if reservation.get('special_occasion'):
        occasion_text = f"\n🎉 Ocasión especial: {reservation['special_occasion']}"

    table_text = ""
    if reservation.get('table_id'):
        table_text = f"\n🪑 Mesa asignada: {reservation['table_id']} ({reservation.get('table_zone', 'salón')})"

    # Preferencias (si existen)
    preferences_text = ""
    if reservation.get('preferences'):
        preferences_text = f"\n📝 Preferencias: {reservation['preferences']}"
    
    # Fecha de creación
    created_at = reservation.get('created_at', 'N/A')
    if created_at != 'N/A':
        try:
            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            created_at = created_dt.strftime("%d/%m/%Y %H:%M")
        except:
            pass
    
    # Mensaje de estado final
    if reservation['status'] == 'cancelled':
        status_message = "\n\n⚠️ Esta reserva está CANCELADA"
    elif reservation['status'] == 'confirmed':
        status_message = "\n\n✨ Reserva confirmada y activa"
    else:
        status_message = "\n\n⏳ Pendiente de confirmación"
    
    # ============================================
    # CONSTRUIR MENSAJE DE DETALLES
    # ============================================
    details = f"""
📋 DETALLES DE LA RESERVA

🆔 ID: {reservation['id']}
👤 Cliente: {reservation['customer_name']}
📞 Teléfono: {reservation['phone']}
📅 Fecha: {full_date}
🕐 Hora: {reservation['time']}
👥 Número de personas: {reservation['num_people']}{table_text}{occasion_text}{preferences_text}
📊 Estado: {status_display}
🗓️ Creada el: {created_at}{status_message}
    """
    
    return details.strip()
