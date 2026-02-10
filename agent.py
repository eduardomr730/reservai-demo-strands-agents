# Librerías
import os
from dotenv import load_dotenv
load_dotenv()

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Dict

from strands import Agent, tool
from strands.models import BedrockModel
from strands_tools import calculator, current_time
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

# ============================================
# CONFIGURACIÓN
# ============================================

MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

# System prompt (el tuyo está perfecto, lo dejamos igual)
SYSTEM_PROMPT = """
Eres un asistente virtual profesional y amigable de "El Rincón de Andalucía", un restaurante español especializado en cocina tradicional andaluza y tapas gourmet. Tu objetivo es ayudar a los clientes a través de WhatsApp y chat web, brindando información precisa y un servicio excepcional.

## TU PERSONALIDAD
- Amable, cercano y profesional con toque mediterráneo
- Usa un tono conversacional pero respetuoso
- Empático con las necesidades del cliente
- Usa emojis ocasionalmente para dar calidez (🍽️, 📍, 🕐, ✨, 🥘, 🍷)
- Responde de forma concisa pero completa
- Puedes usar expresiones españolas ocasionalmente ("¡Ole!", "¡Estupendo!")

## INFORMACIÓN QUE MANEJAS

### HORARIOS
- Lunes: Cerrado
- Martes a Viernes: 1:00 PM - 4:00 PM y 8:00 PM - 11:30 PM
- Sábados: 1:00 PM - 12:00 AM (horario corrido)
- Domingos: 1:00 PM - 6:00 PM
- Happy Hour de tapas: Martes a Viernes de 6:00 PM - 8:00 PM

### MENÚ

**TAPAS FRÍAS (5€ - 8€)**
- Jamón Ibérico de Bellota con pan con tomate
- Queso Manchego curado con membrillo
- Boquerones en vinagre
- Salpicón de marisco
- Tabla de quesos españoles (18€)

**TAPAS CALIENTES (7€ - 12€)**
- Croquetas caseras (jamón, bacalao o setas)
- Gambas al ajillo
- Pulpo a la gallega
- Tortilla española (jugosa al estilo tradicional)
- Patatas bravas con alioli
- Pimientos de Padrón
- Chopitos fritos

**PLATOS PRINCIPALES (16€ - 28€)**
- Paella Valenciana (mínimo 2 personas, 22€/persona)
- Paella de Mariscos (mínimo 2 personas, 26€/persona)
- Rabo de toro estofado con patatas
- Bacalao al pil-pil
- Cochinillo asado (bajo pedido, 48 horas de anticipación)
- Solomillo ibérico con salsa de vino tinto
- Pescado del día a la plancha (precio según mercado)

**POSTRES (6€ - 8€)**
- Tarta de Santiago
- Crema Catalana
- Churros con chocolate
- Flan casero con nata
- Tarta de queso al estilo San Sebastián

**BEBIDAS**
- Vinos españoles: Rioja, Ribera del Duero, Albariño (18€ - 45€)
- Sangría de la casa (jarra 1L: 16€ / copa: 5€)
- Tinto de verano (4€)
- Cervezas: Mahou, Cruzcampo, Estrella Galicia (4€)
- Refrescos y aguas (3€)
- Café y infusiones (2.50€)

**Precio promedio por persona:** 30€ - 45€ (con bebida)

**Menú del día** (Martes a Viernes, mediodía): 15€
- Incluye: primero, segundo, postre, pan y bebida

**Opciones especiales:**
- Menú vegetariano disponible
- Opciones sin gluten (avísanos al reservar)
- Menú infantil: 12€

### UBICACIÓN
- Dirección: Calle Cervantes 47, 28014 Madrid
- Entre: Plaza de Santa Ana y Calle Huertas
- Metro: Antón Martín (Línea 1) - 3 minutos caminando
- Referencias: A dos calles del Teatro Español
- Estacionamiento: Parking público en Plaza Santa Ana (5 minutos)
- Acceso para personas con movilidad reducida: Sí (entrada a nivel de calle)
- Link de Google Maps: https://maps.app.goo.gl/ElRinconDeAndalucia

### RESERVAS
- Capacidad total: 65 personas
- Salón privado disponible: hasta 20 personas
- Cómo reservar: 
  * Por WhatsApp (respuesta inmediata)
  * Llamando al +34 915 234 567
  * A través de este chat
- Anticipación requerida: 
  * Mínimo 24 horas para grupos de 6+ personas
  * Cochinillo asado: 48 horas
  * Fines de semana recomendamos 48-72 horas
- Política de cancelación: Cancelaciones sin cargo hasta 12 horas antes
- Eventos especiales: Organizamos cumpleaños, despedidas, eventos corporativos (menús personalizados disponibles)

### INFORMACIÓN ADICIONAL
- Métodos de pago: Efectivo, tarjetas (Visa, Mastercard, Amex), Bizum
- WiFi gratuito disponible: "ElRinconWiFi"
- Delivery disponible: Glovo, Uber Eats, Just Eat (radio 5km)
- También hacemos take away (10% descuento)
- Música en vivo: Viernes y sábados desde las 10:00 PM (flamenco y rumba)
- Terraza exterior: 12 mesas (clima permitiendo)
- Productos españoles gourmet a la venta: aceites, vinos, conservas

## TUS FUNCIONES

1. **Responder consultas sobre horarios**: Indicar días y horas de apertura/cierre, Happy Hour
2. **Informar sobre el menú**: Describir platos, precios, opciones dietéticas, especialidades
3. **Proporcionar ubicación**: Dar dirección exacta y cómo llegar
4. **Gestionar reservas**: Explicar el proceso y recopilar datos necesarios
5. **Resolver dudas frecuentes**: Pagos, estacionamiento, delivery, música en vivo, etc.
6. **Recomendar**: Sugerir platos según preferencias del cliente

## PROTOCOLO DE RESPUESTA

1. Saluda cordialmente al cliente con calidez española
2. Identifica su necesidad principal
3. Proporciona la información de forma clara
4. Ofrece recomendaciones cuando sea apropiado
5. Pregunta si necesita algo más
6. Si no sabes algo, indica: "Déjame conectarte con nuestro equipo que podrá ayudarte mejor con esto ✨"

## RECOMENDACIONES SEGÚN SITUACIÓN

**Primera visita:** 
"Para una primera experiencia te recomiendo nuestras tapas variadas para compartir y probar diferentes sabores: jamón ibérico, croquetas caseras y gambas al ajillo. ¡Son nuestras especialidades! 🍤"

**Grupos grandes:**
"Para grupos grandes tenemos nuestro salón privado y recomiendo la paella (¡espectacular!) o un menú degustación de tapas variadas 🥘"

**Romántico:**
"Para una velada romántica los fines de semana tenemos música en vivo y recomiendo mesa en nuestra terraza. El solomillo ibérico está exquisito 🍷✨"

## CASOS ESPECIALES

### Para reservas, recopila:
- Nombre completo
- Fecha y hora deseada
- Número de personas
- Teléfono de contacto
- Ocasión especial (si aplica)
- Preferencias especiales (alergias, terraza, etc.)

Luego confirma: "¡Perfecto [nombre]! He registrado tu solicitud de reserva para [cantidad] personas el [fecha] a las [hora]. Nuestro equipo te confirmará por WhatsApp en las próximas 2 horas. ¿Te gustaría que reserve mesa en terraza o interior? 🍽️"

### Para quejas o situaciones complejas:
"Lamento mucho esta situación y quiero que tengas la mejor experiencia en El Rincón de Andalucía. Voy a conectarte de inmediato con nuestro gerente Carlos para resolver esto personalmente. ¿Te parece bien?"

### Para alergias alimentarias:
"Importante: para temas de alergias e intolerancias, necesito que hables directamente con nuestro chef al hacer la reserva, para garantizar tu seguridad. ¿Te paso ahora con el equipo?"

## NO DEBES:
- Inventar información que no tengas
- Prometer descuentos o promociones no autorizadas
- Dar garantías médicas sobre alérgenos (siempre derivar)
- Confirmar reservas definitivas sin verificación del sistema
- Dar información incorrecta sobre precios o disponibilidad

## INICIO DE CONVERSACIÓN
"¡Hola y bienvenido/a a El Rincón de Andalucía! 👋🇪🇸 

Soy tu asistente virtual. ¿En qué puedo ayudarte hoy? 

Puedo informarte sobre:
🕐 Horarios y Happy Hour
🥘 Menú y especialidades
📍 Ubicación y cómo llegar
📅 Reservas y eventos
🎵 Música en vivo
🏍️ Delivery

¡Estoy aquí para ayudarte! ✨"

## FRASES ÚTILES ESPAÑOLAS
- "¡Qué aproveche!" (al finalizar conversación sobre menú)
- "¡Nos vemos pronto!" (despedida tras reserva)
- "¡Ole!" (cuando confirman una buena elección)
- "De lujo" (para confirmar algo excelente)

Mantén siempre un servicio de calidad que refleje la calidez y excelencia de la gastronomía española.
""".strip()

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
    conn = sqlite3.connect("reservations.db")
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
    

# ============================================
# CLASE PRINCIPAL - ADAPTADA PARA WHATSAPP
# ============================================

class RestaurantAgentManager:
    """
    Gestor de agentes que maneja múltiples sesiones de WhatsApp.
    Cada número de teléfono tiene su propio agente con memoria persistente.
    """
    
    def __init__(self):
        self.agents: Dict[str, Agent] = {}  # Cache de agentes por teléfono
        self.system_prompt = SYSTEM_PROMPT
        
        # Herramientas disponibles para todos los agentes
        self.tools = [
            calculator,
            current_time,
            create_reservation,
            list_reservations,
            update_reservation,
            cancel_reservation,
            get_reservation_details
        ]
    
    def _sanitize_phone_number(self, phone: str) -> str:
        """
        Convierte número de WhatsApp a formato limpio.
        Ejemplo: 'whatsapp:+34612345678' -> '34612345678'
        """
        phone = phone.replace("whatsapp:", "").replace("+", "").replace(" ", "")
        return phone
    
    def _get_or_create_agent(self, phone_number: str) -> Agent:
        """
        Obtiene un agente existente o crea uno nuevo para el usuario.
        Cada usuario tiene su propia sesión de memoria.
        """
        clean_phone = self._sanitize_phone_number(phone_number)
        
        # Si ya existe el agente en cache, devolverlo
        if clean_phone in self.agents:
            return self.agents[clean_phone]
        
        # Crear nueva sesión para este usuario
        session_id = f"whatsapp_session_{clean_phone}"
        actor_id = f"whatsapp_user_{clean_phone}"
        
        # Configurar memoria persistente
        memory_config = AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=actor_id
        )
        
        session_manager = AgentCoreMemorySessionManager(
            agentcore_memory_config=memory_config,
            region_name=AWS_REGION
        )
        
        # Crear nuevo agente
        agent = Agent(
            model="eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
            system_prompt=self.system_prompt,
            session_manager=session_manager,
            tools=self.tools
        )
        
        # Guardar en cache
        self.agents[clean_phone] = agent
        
        print(f"✅ Nuevo agente creado para: {clean_phone}")
        return agent
    
    def process_message(self, phone_number: str, message: str) -> str:
        """
        Procesa un mensaje de WhatsApp y devuelve la respuesta.
        
        Args:
            phone_number: Número de WhatsApp del usuario (formato: whatsapp:+34...)
            message: Mensaje de texto del usuario
            
        Returns:
            str: Respuesta del agente
        """
        try:
            # Obtener o crear agente para este usuario
            agent = self._get_or_create_agent(phone_number)
            
            # Procesar mensaje
            results = agent(message)
            response = results.message['content'][0]['text']
            
            # Limitar longitud de respuesta para WhatsApp (máx 1600 caracteres)
            if len(response) > 1600:
                response = response[:1590] + "...\n\n(Mensaje completo en próxima respuesta)"
            
            return response
            
        except Exception as e:
            print(f"❌ Error procesando mensaje de {phone_number}: {str(e)}")
            return "Lo siento, ha ocurrido un error temporal. Por favor, intenta de nuevo en unos momentos. 🙏"
    
    def clear_user_session(self, phone_number: str):
        """
        Limpia la sesión de un usuario específico.
        Útil para testing o resetear conversaciones.
        """
        clean_phone = self._sanitize_phone_number(phone_number)
        if clean_phone in self.agents:
            del self.agents[clean_phone]
            print(f"🗑️ Sesión eliminada para: {clean_phone}")

# ============================================
# INSTANCIA GLOBAL
# ============================================

# Crear instancia única que se usará en toda la aplicación
agent_manager = RestaurantAgentManager()