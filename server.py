import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
import logging
from datetime import datetime

# Importar nuestro agente
from agent import agent_manager

# ============================================
# CONFIGURACIÓN
# ============================================

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Credenciales de Twilio (para validar requests)
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
VALIDATE_TWILIO = os.environ.get("VALIDATE_TWILIO", "true").lower() == "true"

# Crear aplicación FastAPI
app = FastAPI(
    title="El Rincón de Andalucía - WhatsApp Bot",
    description="Asistente virtual para reservas de restaurante",
    version="1.0.0"
)

# ============================================
# ENDPOINTS
# ============================================

@app.get("/")
async def root():
    """
    Endpoint raíz - Verificar que el servidor está funcionando
    """
    return {
        "status": "online",
        "service": "El Rincón de Andalucía WhatsApp Bot",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "health": "/health",
            "whatsapp_webhook": "/whatsapp (POST)"
        }
    }

@app.get("/health")
async def health_check():
    """
    Health check - Útil para monitoring y deploy
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(None),
    NumMedia: str = Form(default="0")
):
    """
    Webhook principal para recibir mensajes de WhatsApp vía Twilio.
    
    Parámetros que Twilio envía:
    - From: Número de teléfono del usuario (ej: whatsapp:+34612345678)
    - Body: Mensaje de texto del usuario
    - MessageSid: ID único del mensaje de Twilio
    - NumMedia: Número de archivos multimedia adjuntos
    """
    
    # Log del mensaje recibido
    logger.info(f"📱 Mensaje recibido de {From}")
    logger.info(f"💬 Contenido: {Body}")
    logger.info(f"🆔 MessageSid: {MessageSid}")
    
    # Validar que el request viene de Twilio (seguridad)
    if VALIDATE_TWILIO and TWILIO_AUTH_TOKEN:
        is_valid = await validate_twilio_request(request)
        if not is_valid:
            logger.warning(f"⚠️ Request inválido de {From}")
            raise HTTPException(status_code=403, detail="Invalid request signature")
    
    # Crear respuesta de Twilio
    twilio_response = MessagingResponse()
    
    try:
        # Verificar si hay archivos multimedia
        num_media = int(NumMedia) if NumMedia else 0
        if num_media > 0:
            logger.info(f"📎 Usuario envió {num_media} archivos multimedia")
            response_text = "Disculpa, actualmente solo puedo procesar mensajes de texto. Por favor escribe tu consulta. 📝"
            twilio_response.message(response_text)
            return Response(content=str(twilio_response), media_type="application/xml")
        
        # Verificar que el mensaje no esté vacío
        if not Body or Body.strip() == "":
            logger.warning(f"⚠️ Mensaje vacío de {From}")
            response_text = "No recibí ningún mensaje. Por favor escribe tu consulta. 😊"
            twilio_response.message(response_text)
            return Response(content=str(twilio_response), media_type="application/xml")
        
        # Procesar el mensaje con nuestro agente
        logger.info(f"🤖 Procesando con agente...")
        response_text = agent_manager.process_message(
            phone_number=From,
            message=Body.strip()
        )
        
        logger.info(f"✅ Respuesta generada: {response_text[:100]}...")
        
        # Si la respuesta es muy larga, dividirla en múltiples mensajes
        max_length = 1600  # WhatsApp limit
        if len(response_text) > max_length:
            # Dividir por párrafos primero
            paragraphs = response_text.split('\n\n')
            current_message = ""
            
            for paragraph in paragraphs:
                if len(current_message) + len(paragraph) + 2 <= max_length:
                    current_message += paragraph + "\n\n"
                else:
                    # Enviar mensaje actual
                    if current_message:
                        twilio_response.message(current_message.strip())
                    # Empezar nuevo mensaje
                    current_message = paragraph + "\n\n"
            
            # Enviar último mensaje
            if current_message:
                twilio_response.message(current_message.strip())
        else:
            # Mensaje único
            twilio_response.message(response_text)
        
        # Retornar respuesta XML de Twilio
        return Response(
            content=str(twilio_response),
            media_type="application/xml"
        )
        
    except Exception as e:
        logger.error(f"❌ Error procesando mensaje de {From}: {str(e)}", exc_info=True)
        
        # Respuesta de error amigable
        error_message = (
            "Disculpa, he tenido un problema técnico temporal. 😔\n\n"
            "Por favor intenta de nuevo en unos momentos, o llámanos al +34 915 234 567.\n\n"
            "¡Gracias por tu paciencia!"
        )
        twilio_response.message(error_message)
        
        return Response(
            content=str(twilio_response),
            media_type="application/xml"
        )

async def validate_twilio_request(request: Request) -> bool:
    """
    Valida que el request provenga realmente de Twilio (seguridad).
    Verifica la firma X-Twilio-Signature.
    """
    try:
        logger.info(f"Request headers: {request.headers}")
        # Obtener la firma de Twilio
        signature = request.headers.get("X-Twilio-Signature", "")
        
        # Obtener la URL completa del request
        url = str(request.url)
        
        # Obtener los parámetros del formulario
        form_data = await request.form()
        params = {key: value for key, value in form_data.items()}
        
        # Validar con el validador de Twilio
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        is_valid = validator.validate(url, params, signature)
        
        return is_valid
        
    except Exception as e:
        logger.error(f"Error validando request de Twilio: {str(e)}")
        return False

# ============================================
# ENDPOINTS ADICIONALES (útiles para debugging)
# ============================================

@app.post("/test-message")
async def test_message(
    phone: str = Form(...),
    message: str = Form(...)
):
    """
    Endpoint de prueba para simular mensajes sin necesidad de Twilio.
    Útil para desarrollo local.
    
    Uso:
    curl -X POST http://localhost:8000/test-message \
      -d "phone=whatsapp:+34612345678" \
      -d "message=Hola, quiero hacer una reserva"
    """
    try:
        response = agent_manager.process_message(phone, message)
        return {
            "status": "success",
            "phone": phone,
            "message": message,
            "response": response
        }
    except Exception as e:
        logger.error(f"Error en test-message: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }

@app.get("/stats")
async def get_stats():
    """
    Obtener estadísticas básicas del servidor.
    """
    import sqlite3
    
    try:
        conn = sqlite3.connect('reservations.db')
        cursor = conn.cursor()
        
        # Contar reservas por estado
        cursor.execute("""
            SELECT status, COUNT(*) 
            FROM reservations 
            GROUP BY status
        """)
        reservations_by_status = dict(cursor.fetchall())
        
        # Contar total de reservas
        cursor.execute("SELECT COUNT(*) FROM reservations")
        total_reservations = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "status": "success",
            "stats": {
                "total_reservations": total_reservations,
                "by_status": reservations_by_status,
                "active_users": len(agent_manager.agents)
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

# ============================================
# EVENTO DE INICIO
# ============================================

@app.on_event("startup")
async def startup_event():
    """
    Se ejecuta cuando inicia el servidor.
    """
    logger.info("=" * 60)
    logger.info("🚀 Servidor iniciado")
    logger.info("📍 Servicio: El Rincón de Andalucía WhatsApp Bot")
    logger.info("=" * 60)
    
    # Verificar variables de entorno críticas
    required_vars = ["AGENTCORE_MEMORY_ID", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        logger.warning(f"⚠️ Variables de entorno faltantes: {', '.join(missing_vars)}")
    else:
        logger.info("✅ Todas las variables de entorno configuradas")
    
    if VALIDATE_TWILIO and not TWILIO_AUTH_TOKEN:
        logger.warning("⚠️ TWILIO_AUTH_TOKEN no configurado, validación deshabilitada")

@app.on_event("shutdown")
async def shutdown_event():
    """
    Se ejecuta cuando se detiene el servidor.
    """
    logger.info("👋 Servidor detenido")

# ============================================
# EJECUTAR DIRECTAMENTE (desarrollo local)
# ============================================

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("PORT", 8000))
    
    logger.info(f"🌐 Iniciando servidor en http://localhost:{port}")
    logger.info(f"📱 Webhook WhatsApp: http://localhost:{port}/whatsapp")
    logger.info(f"🧪 Test endpoint: http://localhost:{port}/test-message")
    
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=True,  # Auto-reload en desarrollo
        log_level="info"
    )