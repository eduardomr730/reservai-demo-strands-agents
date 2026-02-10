"""
Middleware para validación de requests de Twilio.
"""
import logging
from fastapi import Request, HTTPException
from twilio.request_validator import RequestValidator
from app.config import settings

logger = logging.getLogger(__name__)


class TwilioValidator:
    """Validador de requests de Twilio."""
    
    def __init__(self):
        self.validator = RequestValidator(settings.twilio_auth_token)
    
    async def validate_request(self, request: Request) -> bool:
        """
        Valida que el request provenga realmente de Twilio.
        Maneja correctamente proxies (Railway, Render, etc.)
        """
        if not settings.validate_twilio:
            logger.warning("⚠️ Validación de Twilio deshabilitada")
            return True
        
        try:
            # Obtener la firma de Twilio
            signature = request.headers.get("x-twilio-signature", "")
            
            if not signature:
                logger.warning("⚠️ No se encontró x-twilio-signature en headers")
                return False
            
            # Construir URL correcta considerando proxies
            proto = request.headers.get("x-forwarded-proto", "https")
            host = request.headers.get("x-forwarded-host", 
                                      request.headers.get("host", ""))
            path = request.url.path
            url = f"{proto}://{host}{path}"
            
            logger.debug(f"🔐 Validando URL: {url}")
            
            # Obtener parámetros del formulario
            form_data = await request.form()
            params = {key: value for key, value in form_data.items()}
            
            # Validar
            is_valid = self.validator.validate(url, params, signature)
            
            if is_valid:
                logger.debug("✅ Request de Twilio válido")
            else:
                logger.warning("⚠️ Request de Twilio inválido")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"❌ Error validando request: {e}")
            return False
    
    async def require_valid_twilio_request(self, request: Request):
        """Middleware que rechaza requests inválidos."""
        is_valid = await self.validate_request(request)
        
        if not is_valid and settings.environment == "production":
            raise HTTPException(
                status_code=403, 
                detail="Invalid Twilio request signature"
            )


# Instancia global
twilio_validator = TwilioValidator()
