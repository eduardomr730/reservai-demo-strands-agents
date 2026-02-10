"""
Servidor FastAPI para el bot de WhatsApp de El Rincón de Andalucía.
"""
import logging
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import Response, JSONResponse
from twilio.twiml.messaging_response import MessagingResponse

from app.config import settings
from app.agent.manager import agent_manager
from app.agent.prompts import ERROR_MESSAGES
from app.middleware.validation import twilio_validator

# Configurar logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Crear aplicación
app = FastAPI(
    title="El Rincón de Andalucía - WhatsApp Bot",
    description="Asistente virtual para reservas de restaurante",
    version="2.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None
)


# ============================================
# ENDPOINTS PRINCIPALES
# ============================================

@app.get("/")
async def root():
    """Endpoint raíz - Verificar que el servidor está funcionando."""
    return {
        "status": "online",
        "service": "El Rincón de Andalucía WhatsApp Bot",
        "version": "2.0.0",
        "environment": settings.environment,
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "health": "/health",
            "whatsapp_webhook": "/whatsapp (POST)",
            "stats": "/stats (GET)"
        }
    }


@app.get("/health")
async def health_check():
    """Health check - Útil para monitoring y Railway."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_sessions": agent_manager.get_active_sessions_count()
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
    """
    logger.info(f"📱 Mensaje recibido de {From}")
    logger.debug(f"💬 Contenido: {Body}")
    logger.debug(f"🆔 MessageSid: {MessageSid}")
    
    # Validar request de Twilio (solo en producción)
    if settings.environment == "production":
        try:
            await twilio_validator.require_valid_twilio_request(request)
        except HTTPException as e:
            logger.warning(f"⚠️ Request inválido rechazado: {e.detail}")
            # En producción, rechazamos
            if settings.validate_twilio:
                raise
            # En desarrollo, solo advertimos
            logger.warning("⚠️ Continuando a pesar de firma inválida (modo desarrollo)")
    
    # Crear respuesta de Twilio
    twilio_response = MessagingResponse()
    
    try:
        # Verificar multimedia
        num_media = int(NumMedia) if NumMedia else 0
        if num_media > 0:
            logger.info(f"📎 Usuario envió {num_media} archivos multimedia")
            twilio_response.message(ERROR_MESSAGES["media_not_supported"])
            return Response(
                content=str(twilio_response),
                media_type="application/xml"
            )
        
        # Verificar mensaje vacío
        if not Body or Body.strip() == "":
            logger.warning(f"⚠️ Mensaje vacío de {From}")
            twilio_response.message(ERROR_MESSAGES["empty_message"])
            return Response(
                content=str(twilio_response),
                media_type="application/xml"
            )
        
        # Procesar con el agente
        logger.info("🤖 Procesando con agente...")
        response_text = agent_manager.process_message(
            phone_number=From,
            message=Body.strip()
        )
        
        logger.info(f"✅ Respuesta generada: {len(response_text)} caracteres")
        
        # Si la respuesta es muy larga, dividirla
        if len(response_text) > settings.max_message_length:
            # Dividir por párrafos
            paragraphs = response_text.split('\n\n')
            current_message = ""
            
            for paragraph in paragraphs:
                if len(current_message) + len(paragraph) + 2 <= settings.max_message_length:
                    current_message += paragraph + "\n\n"
                else:
                    if current_message:
                        twilio_response.message(current_message.strip())
                    current_message = paragraph + "\n\n"
            
            if current_message:
                twilio_response.message(current_message.strip())
        else:
            twilio_response.message(response_text)
        
        return Response(
            content=str(twilio_response),
            media_type="application/xml"
        )
        
    except Exception as e:
        logger.error(f"❌ Error procesando mensaje de {From}: {e}", exc_info=True)
        
        # Respuesta de error amigable
        twilio_response.message(ERROR_MESSAGES["technical_error"])
        
        return Response(
            content=str(twilio_response),
            media_type="application/xml"
        )


# ============================================
# ENDPOINTS ADMINISTRATIVOS
# ============================================

@app.post("/test-message")
async def test_message(phone: str = Form(...), message: str = Form(...)):
    """
    Endpoint de prueba para simular mensajes sin Twilio.
    Solo disponible en desarrollo.
    """
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="Not found")
    
    try:
        response = agent_manager.process_message(phone, message)
        return {
            "status": "success",
            "phone": phone,
            "message": message,
            "response": response
        }
    except Exception as e:
        logger.error(f"Error en test-message: {e}")
        return {"status": "error", "error": str(e)}


@app.get("/stats")
async def get_stats():
    """Obtener estadísticas básicas del servidor."""
    try:
        import sqlite3
        from app.config import settings
        
        conn = sqlite3.connect(settings.database_path)
        cursor = conn.cursor()
        
        # Contar reservas por estado
        cursor.execute("""
            SELECT status, COUNT(*) 
            FROM reservations 
            GROUP BY status
        """)
        reservations_by_status = dict(cursor.fetchall())
        
        # Total de reservas
        cursor.execute("SELECT COUNT(*) FROM reservations")
        total_reservations = cursor.fetchone()[0]
        
        # Reservas hoy
        cursor.execute("""
            SELECT COUNT(*) FROM reservations 
            WHERE date = date('now')
        """)
        today_reservations = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "stats": {
                "total_reservations": total_reservations,
                "today_reservations": today_reservations,
                "by_status": reservations_by_status,
                "active_users": agent_manager.get_active_sessions_count()
            }
        }
    except Exception as e:
        logger.error(f"Error obteniendo stats: {e}")
        return {"status": "error", "error": str(e)}


@app.post("/admin/clear-session")
async def clear_session(phone: str = Form(...)):
    """Limpiar sesión de un usuario (solo desarrollo)."""
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="Not found")
    
    success = agent_manager.clear_user_session(phone)
    return {
        "status": "success" if success else "not_found",
        "phone": phone
    }


# ============================================
# EVENTOS
# ============================================

@app.on_event("startup")
async def startup_event():
    """Se ejecuta cuando inicia el servidor."""
    logger.info("=" * 70)
    logger.info("🚀 Servidor iniciado")
    logger.info(f"📍 Servicio: El Rincón de Andalucía WhatsApp Bot v2.0")
    logger.info(f"🌍 Entorno: {settings.environment}")
    logger.info(f"🔧 Debug: {settings.debug}")
    logger.info(f"📊 Workers: {settings.workers}")
    logger.info("=" * 70)
    
    # Verificar configuración crítica
    if not settings.agentcore_memory_id:
        logger.error("❌ AGENTCORE_MEMORY_ID no configurado")
    
    if not settings.twilio_auth_token:
        logger.error("❌ TWILIO_AUTH_TOKEN no configurado")
    
    logger.info("✅ Todas las variables críticas configuradas")


@app.on_event("shutdown")
async def shutdown_event():
    """Se ejecuta cuando se detiene el servidor."""
    logger.info("👋 Servidor detenido")
    logger.info(f"📊 Sesiones activas al cerrar: {agent_manager.get_active_sessions_count()}")


# ============================================
# EJECUTAR DIRECTAMENTE (desarrollo)
# ============================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"🌐 Iniciando servidor en http://{settings.host}:{settings.port}")
    logger.info(f"📱 Webhook WhatsApp: http://{settings.host}:{settings.port}/whatsapp")
    logger.info(f"🧪 Test endpoint: http://{settings.host}:{settings.port}/test-message")
    
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )