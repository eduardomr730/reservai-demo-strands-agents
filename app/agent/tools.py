"""
Herramientas (tools) para el agente.
"""
import os
import sqlite3
import uuid
import json
from datetime import datetime
from pathlib import Path
from strands import tool
from app.config import settings

# Asegurar que existe la carpeta data
Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)


def get_db_connection():
    """Obtener conexión a la base de datos."""
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Inicializar base de datos con tabla de reservas."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            num_people INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            special_occasion TEXT,
            preferences TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()


# Inicializar DB al importar
init_database()

@tool
def create_reservation(date: str, time: str, num_people: int, customer_name: str, phone: str, special_occasion: str = "", preferences: str = "") -> str:
    """
    Create a new restaurant reservation in the database.

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
    # Validate date format
    try:
        reservation_date = datetime.strptime(date, "%Y-%m-%d")
        day_of_week = reservation_date.weekday()  # 0=Monday, 6=Sunday
    except ValueError:
        raise ValueError("Date must be in format 'YYYY-MM-DD'")

    # Validate time format
    try:
        reservation_time = datetime.strptime(time, "%H:%M")
        hour = reservation_time.hour
        minute = reservation_time.minute
    except ValueError:
        raise ValueError("Time must be in format 'HH:MM'")

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

    # Validate phone format (basic validation)
    if not phone or len(phone) < 9:
        raise ValueError("Por favor proporciona un número de teléfono válido")

    # Generate a unique reservation ID (format: RES-YYYYMMDD-XXXX)
    date_part = reservation_date.strftime("%Y%m%d")
    unique_part = str(uuid.uuid4())[:8].upper()
    reservation_id = f"RES-{date_part}-{unique_part}"

    # Database connection
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create the reservations table if it doesn't exist
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reservations (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            num_people INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            special_occasion TEXT,
            preferences TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Insert the reservation
    cursor.execute(
        """
        INSERT INTO reservations 
        (id, date, time, num_people, customer_name, phone, special_occasion, preferences, status) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (reservation_id, date, time, num_people, customer_name, phone, special_occasion, preferences, "pending")
    )

    conn.commit()
    conn.close()

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
📞 Teléfono: {phone}{occasion_text}{preferences_text}

⏳ Estado: Pendiente de confirmación

Nuestro equipo te confirmará la reserva por WhatsApp en las próximas 2 horas.

¡Nos vemos pronto en El Rincón de Andalucía! 🇪🇸✨
    """
    
    return confirmation_message.strip()

@tool
def list_reservations(date: str = "", status: str = "all", customer_name: str = "") -> str:
    """
    List all restaurant reservations from the database with optional filters.
    
    Args:
        date (str, optional): Filter by specific date (format: YYYY-MM-DD). Leave empty for all dates.
        status (str, optional): Filter by status ('pending', 'confirmed', 'cancelled', 'all'). Default: 'all'.
        customer_name (str, optional): Filter by customer name (partial match). Leave empty for all customers.
    
    Returns:
        str: JSON string with the list of reservations or a message if none found.
    """
    # Check if database exists
    if not os.path.exists('reservations.db'):
        return "No hay reservas disponibles en el sistema"
    
    conn = sqlite3.connect('reservations.db')
    conn.row_factory = sqlite3.Row  # This enables column access by name
    cursor = conn.cursor()
    
    # Check if the reservations table exists
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reservations'")
        if not cursor.fetchone():
            conn.close()
            return "No hay reservas disponibles en el sistema"
        
        # Build dynamic query based on filters
        query = "SELECT * FROM reservations WHERE 1=1"
        params = []
        
        # Filter by date
        if date:
            try:
                # Validate date format
                datetime.strptime(date, "%Y-%m-%d")
                query += " AND date = ?"
                params.append(date)
            except ValueError:
                conn.close()
                return "Formato de fecha inválido. Usa YYYY-MM-DD"
        
        # Filter by status
        if status.lower() != "all":
            if status.lower() in ['pending', 'confirmed', 'cancelled']:
                query += " AND status = ?"
                params.append(status.lower())
            else:
                conn.close()
                return "Estado inválido. Usa: 'pending', 'confirmed', 'cancelled' o 'all'"
        
        # Filter by customer name (partial match, case-insensitive)
        if customer_name:
            query += " AND LOWER(customer_name) LIKE ?"
            params.append(f"%{customer_name.lower()}%")
        
        # Order by date and time
        query += " ORDER BY date, time"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # If no reservations found
        if not rows:
            conn.close()
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
        
        # Convert rows to dictionaries with enhanced formatting
        reservations = []
        for row in rows:
            # Format date for better readability
            try:
                date_obj = datetime.strptime(row['date'], "%Y-%m-%d")
                formatted_date = date_obj.strftime("%d/%m/%Y")
                day_name = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][date_obj.weekday()]
                full_date = f"{day_name}, {formatted_date}"
            except:
                full_date = row['date']
            
            # Status emoji
            status_emoji = {
                'pending': '⏳',
                'confirmed': '✅',
                'cancelled': '❌'
            }.get(row['status'], '📋')
            
            reservation = {
                'id': row['id'],
                'date': row['date'],
                'formatted_date': full_date,
                'time': row['time'],
                'num_people': row['num_people'],
                'customer_name': row['customer_name'],
                'phone': row['phone'],
                'special_occasion': row['special_occasion'] or '',
                'preferences': row['preferences'] or '',
                'status': row['status'],
                'status_display': f"{status_emoji} {row['status'].capitalize()}",
                'created_at': row['created_at']
            }
            reservations.append(reservation)
        
        conn.close()
        
        # Create a summary header
        summary = {
            'total_reservations': len(reservations),
            'filters_applied': {
                'date': date if date else 'todas las fechas',
                'status': status,
                'customer_name': customer_name if customer_name else 'todos los clientes'
            },
            'reservations': reservations
        }
        
        return json.dumps(summary, ensure_ascii=False, indent=2)
    
    except sqlite3.Error as e:
        conn.close()
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
    # Check if database exists
    if not os.path.exists('reservations.db'):
        return "❌ No hay reservas en el sistema"
    
    conn = sqlite3.connect('reservations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Check if reservation exists
        cursor.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
        existing_reservation = cursor.fetchone()
        
        if not existing_reservation:
            conn.close()
            return f"❌ No se encontró la reserva con ID: {reservation_id}"
        
        # Prepare update fields
        updates = []
        params = []
        changes_made = []
        
        # Validate and update date
        if new_date:
            try:
                reservation_date = datetime.strptime(new_date, "%Y-%m-%d")
                day_of_week = reservation_date.weekday()
                
                # Check if restaurant is closed (Monday = 0)
                if day_of_week == 0:
                    conn.close()
                    return "❌ El restaurante está cerrado los lunes. Por favor elige otro día."
                
                updates.append("date = ?")
                params.append(new_date)
                changes_made.append(f"📅 Fecha: {existing_reservation['date']} → {new_date}")
            except ValueError:
                conn.close()
                return "❌ Formato de fecha inválido. Usa YYYY-MM-DD"
        
        # Validate and update time
        if new_time:
            try:
                reservation_time = datetime.strptime(new_time, "%H:%M")
                hour = reservation_time.hour
                minute = reservation_time.minute
                
                # Use existing date or new date for validation
                check_date = new_date if new_date else existing_reservation['date']
                day_of_week = datetime.strptime(check_date, "%Y-%m-%d").weekday()
                
                # Validate restaurant hours based on day
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
                    conn.close()
                    return f"❌ {error_msg}"
                
                updates.append("time = ?")
                params.append(new_time)
                changes_made.append(f"🕐 Hora: {existing_reservation['time']} → {new_time}")
            except ValueError:
                conn.close()
                return "❌ Formato de hora inválido. Usa HH:MM"
        
        # Validate and update number of people
        if new_num_people > 0:
            if new_num_people < 1:
                conn.close()
                return "❌ El número de personas debe ser al menos 1"
            if new_num_people > 20:
                conn.close()
                return "❌ Para grupos mayores a 20 personas, contacta directamente al restaurante"
            
            updates.append("num_people = ?")
            params.append(new_num_people)
            changes_made.append(f"👥 Personas: {existing_reservation['num_people']} → {new_num_people}")
        
        # Update phone
        if new_phone:
            if len(new_phone) < 9:
                conn.close()
                return "❌ Número de teléfono inválido"
            
            updates.append("phone = ?")
            params.append(new_phone)
            changes_made.append(f"📞 Teléfono: {existing_reservation['phone']} → {new_phone}")
        
        # Update special occasion
        if new_special_occasion:
            updates.append("special_occasion = ?")
            params.append(new_special_occasion)
            old_occasion = existing_reservation['special_occasion'] or 'Ninguna'
            changes_made.append(f"🎉 Ocasión especial: {old_occasion} → {new_special_occasion}")
        
        # Update preferences
        if new_preferences:
            updates.append("preferences = ?")
            params.append(new_preferences)
            old_prefs = existing_reservation['preferences'] or 'Ninguna'
            changes_made.append(f"📝 Preferencias: {old_prefs} → {new_preferences}")
        
        # Update status
        if status:
            status_lower = status.lower()
            if status_lower not in ['pending', 'confirmed', 'cancelled']:
                conn.close()
                return "❌ Estado inválido. Usa: 'pending', 'confirmed' o 'cancelled'"
            
            updates.append("status = ?")
            params.append(status_lower)
            
            status_emoji = {
                'pending': '⏳ Pendiente',
                'confirmed': '✅ Confirmada',
                'cancelled': '❌ Cancelada'
            }
            
            old_status = status_emoji.get(existing_reservation['status'], existing_reservation['status'])
            new_status = status_emoji.get(status_lower, status_lower)
            changes_made.append(f"Estado: {old_status} → {new_status}")
        
        # Check if there are changes to make
        if not updates:
            conn.close()
            return "⚠️ No se especificaron cambios para realizar"
        
        # Execute update
        update_query = f"UPDATE reservations SET {', '.join(updates)} WHERE id = ?"
        params.append(reservation_id)
        
        cursor.execute(update_query, params)
        conn.commit()
        
        # Get updated reservation
        cursor.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
        updated_reservation = cursor.fetchone()
        conn.close()
        
        # Format confirmation message
        status_emoji_map = {
            'pending': '⏳',
            'confirmed': '✅',
            'cancelled': '❌'
        }
        
        status_display = f"{status_emoji_map.get(updated_reservation['status'], '📋')} {updated_reservation['status'].capitalize()}"
        
        # Format date
        try:
            date_obj = datetime.strptime(updated_reservation['date'], "%Y-%m-%d")
            formatted_date = date_obj.strftime("%d/%m/%Y")
            day_name = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][date_obj.weekday()]
            full_date = f"{day_name}, {formatted_date}"
        except:
            full_date = updated_reservation['date']
        
        changes_text = "\n".join([f"  • {change}" for change in changes_made])
        
        occasion_text = f"\n🎉 Ocasión especial: {updated_reservation['special_occasion']}" if updated_reservation['special_occasion'] else ""
        preferences_text = f"\n📝 Preferencias: {updated_reservation['preferences']}" if updated_reservation['preferences'] else ""
        
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
📞 Teléfono: {updated_reservation['phone']}{occasion_text}{preferences_text}
📊 Estado: {status_display}

{'⚠️ Esta reserva ha sido CANCELADA' if updated_reservation['status'] == 'cancelled' else '¡Te esperamos en El Rincón de Andalucía! 🇪🇸✨'}
        """
        
        return confirmation_message.strip()
    
    except sqlite3.Error as e:
        conn.close()
        return f"❌ Error al actualizar la reserva: {str(e)}"
    
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
    # Check if database exists
    if not os.path.exists('reservations.db'):
        return "❌ No hay reservas en el sistema"
    
    conn = sqlite3.connect('reservations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Check if reservation exists
        cursor.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
        reservation = cursor.fetchone()
        
        if not reservation:
            conn.close()
            return f"❌ No se encontró la reserva con ID: {reservation_id}"
        
        # Check if already cancelled
        if reservation['status'] == 'cancelled':
            conn.close()
            return f"⚠️ Esta reserva ya estaba cancelada previamente"
        
        # Update status to cancelled
        if reason:
            cursor.execute(
                "UPDATE reservations SET status = ?, preferences = ? WHERE id = ?",
                ('cancelled', f"{reservation['preferences']} | Motivo cancelación: {reason}", reservation_id)
            )
        else:
            cursor.execute(
                "UPDATE reservations SET status = ? WHERE id = ?",
                ('cancelled', reservation_id)
            )
        
        conn.commit()
        conn.close()
        
        # Format date
        try:
            date_obj = datetime.strptime(reservation['date'], "%Y-%m-%d")
            formatted_date = date_obj.strftime("%d/%m/%Y")
            day_name = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][date_obj.weekday()]
            full_date = f"{day_name}, {formatted_date}"
        except:
            full_date = reservation['date']
        
        reason_text = f"\n💬 Motivo: {reason}" if reason else ""
        
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
    
    except sqlite3.Error as e:
        conn.close()
        return f"❌ Error al cancelar la reserva: {str(e)}"
    
@tool
def get_reservation_details(reservation_id: str) -> str:
    """
    Get detailed information about a specific reservation.
    
    Args:
        reservation_id (str): The unique reservation ID.
    
    Returns:
        str: Detailed reservation information or error message.
    """
    if not os.path.exists('reservations.db'):
        return "❌ No hay reservas en el sistema"
    
    conn = sqlite3.connect('reservations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
        reservation = cursor.fetchone()
        conn.close()
        
        if not reservation:
            return f"❌ No se encontró la reserva con ID: {reservation_id}"
        
        # Format date
        try:
            date_obj = datetime.strptime(reservation['date'], "%Y-%m-%d")
            formatted_date = date_obj.strftime("%d/%m/%Y")
            day_name = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][date_obj.weekday()]
            full_date = f"{day_name}, {formatted_date}"
        except:
            full_date = reservation['date']
        
        status_emoji = {
            'pending': '⏳ Pendiente',
            'confirmed': '✅ Confirmada',
            'cancelled': '❌ Cancelada'
        }
        status_display = status_emoji.get(reservation['status'], reservation['status'])
        
        occasion_text = f"\n🎉 Ocasión especial: {reservation['special_occasion']}" if reservation['special_occasion'] else ""
        preferences_text = f"\n📝 Preferencias: {reservation['preferences']}" if reservation['preferences'] else ""
        
        details = f"""
📋 DETALLES DE LA RESERVA

🆔 ID: {reservation['id']}
👤 Cliente: {reservation['customer_name']}
📞 Teléfono: {reservation['phone']}
📅 Fecha: {full_date}
🕐 Hora: {reservation['time']}
👥 Número de personas: {reservation['num_people']}{occasion_text}{preferences_text}
📊 Estado: {status_display}
🗓️ Creada el: {reservation['created_at']}

{('⚠️ Esta reserva está CANCELADA' if reservation['status'] == 'cancelled' 
  else '✨ Reserva activa' if reservation['status'] == 'confirmed' 
  else '⏳ Pendiente de confirmación')}
        """
        
        return details.strip()
    
    except sqlite3.Error as e:
        conn.close()
        return f"❌ Error al obtener los detalles: {str(e)}"
