"""
Gestor del agente de conversación con memoria persistente.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict
from zoneinfo import ZoneInfo

from strands import Agent
from strands_tools import calculator
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager
)

from app.config import settings
from app.agent.prompts import build_system_prompt, ERROR_MESSAGES
from app.agent.tools import (
    check_availability,
    create_reservation,
    list_reservations,
    update_reservation,
    cancel_reservation,
    get_reservation_details
)

logger = logging.getLogger(__name__)


class RestaurantAgentManager:
    """
    Gestor de agentes que maneja múltiples sesiones de WhatsApp.
    Cada número de teléfono tiene su propio agente con memoria persistente.
    """
    
    def __init__(self):
        self.agents: Dict[str, Agent] = {}
        self.system_prompt = build_system_prompt(self._get_current_datetime_spain())
        
        # Herramientas disponibles
        self.tools = [
            calculator,
            check_availability,
            create_reservation,
            list_reservations,
            update_reservation,
            cancel_reservation,
            get_reservation_details
        ]
        
        logger.info("✅ RestaurantAgentManager inicializado")

    def _now_spain(self) -> datetime:
        """Devuelve la fecha/hora actual en zona horaria de España."""
        return datetime.now(ZoneInfo("Europe/Madrid"))

    def _get_current_datetime_spain(self) -> str:
        """Devuelve fecha y hora actual en España para inyección en prompt."""
        madrid_now = self._now_spain()
        day_names = [
            "Lunes",
            "Martes",
            "Miércoles",
            "Jueves",
            "Viernes",
            "Sábado",
            "Domingo",
        ]
        day_name = day_names[madrid_now.weekday()]
        return f"{day_name} {madrid_now.strftime('%d/%m/%Y %H:%M:%S %Z')}"

    def _get_spain_calendar_context(self) -> str:
        """
        Devuelve un calendario corto (hoy + próximos 7 días) en hora de España.
        """
        madrid_now = self._now_spain()
        day_names = [
            "Lunes",
            "Martes",
            "Miércoles",
            "Jueves",
            "Viernes",
            "Sábado",
            "Domingo",
        ]
        lines = []
        for offset in range(8):
            day_dt = (madrid_now + timedelta(days=offset)).date()
            day_name = day_names[day_dt.weekday()]
            label = "hoy" if offset == 0 else f"+{offset}d"
            lines.append(f"{label}:{day_name} {day_dt.isoformat()}")
        return " | ".join(lines)
    
    def _sanitize_phone_number(self, phone: str) -> str:
        """
        Convierte número de WhatsApp a formato limpio.
        Ejemplo: 'whatsapp:+34612345678' -> '34612345678'
        """
        return phone.replace("whatsapp:", "").replace("+", "").replace(" ", "")

    def _build_message_with_metadata(self, clean_phone: str, message: str) -> str:
        """
        Inyecta metadatos del canal para guiar al agente sin pedir datos redundantes.
        """
        return (
            "[METADATA_WHATSAPP]\n"
            f"telefono_usuario={clean_phone}\n"
            f"fecha_hora_actual_espana={self._get_current_datetime_spain()}\n"
            f"calendario_espana_hoy_mas_7={self._get_spain_calendar_context()}\n"
            "usar_telefono_metadata=true\n"
            "no_solicitar_telefono_al_usuario=true\n"
            "[/METADATA_WHATSAPP]\n\n"
            "[MENSAJE_USUARIO]\n"
            f"{message}\n"
            "[/MENSAJE_USUARIO]"
        )

    def _sanitize_agent_response(self, response: str) -> str:
        """
        Evita exponer identificadores internos o datos técnicos al usuario final.
        """
        sanitized = response
        sanitized = re.sub(r"(?im)^\s*ID:\s*.*(?:\n|$)", "", sanitized)
        sanitized = re.sub(r'(?im)^\s*"id"\s*:\s*".*?"\s*,?\s*$', "", sanitized)
        sanitized = re.sub(r'(?im)^\s*"table_id"\s*:\s*".*?"\s*,?\s*$', "", sanitized)
        sanitized = re.sub(r"(?im)^\s*reservation_id\s*[:=]\s*.*(?:\n|$)", "", sanitized)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
        return sanitized
    
    def _get_or_create_agent(self, phone_number: str) -> Agent:
        """
        Obtiene un agente existente o crea uno nuevo para el usuario.
        Cada usuario tiene su propia sesión de memoria.
        """
        clean_phone = self._sanitize_phone_number(phone_number)
        
        # Si ya existe el agente en cache, devolverlo
        if clean_phone in self.agents:
            logger.debug(f"♻️  Reutilizando agente para {clean_phone}")
            return self.agents[clean_phone]
        
        # Crear nueva sesión para este usuario
        session_id = f"whatsapp_session_{clean_phone}"
        actor_id = f"whatsapp_user_{clean_phone}"
        
        logger.info(f"🆕 Creando nuevo agente para {clean_phone}")
        
        try:
            # Configurar memoria persistente
            memory_config = AgentCoreMemoryConfig(
                memory_id=settings.agentcore_memory_id,
                session_id=session_id,
                actor_id=actor_id
            )
            
            session_manager = AgentCoreMemorySessionManager(
                agentcore_memory_config=memory_config,
                region_name=settings.aws_region
            )
            
            # Crear nuevo agente
            agent = Agent(
                model=settings.agent_model,
                system_prompt=self.system_prompt,
                session_manager=session_manager,
                tools=self.tools
            )
            
            # Guardar en cache
            self.agents[clean_phone] = agent
            
            logger.info(f"✅ Agente creado exitosamente para {clean_phone}")
            return agent
            
        except Exception as e:
            logger.error(f"❌ Error creando agente para {clean_phone}: {e}")
            raise

    def _refresh_agent_system_prompt(self, agent: Agent) -> None:
        """
        Actualiza el prompt del agente en cada ejecución con la hora actual de España.
        """
        dynamic_prompt = build_system_prompt(self._get_current_datetime_spain())
        self.system_prompt = dynamic_prompt
        try:
            agent.system_prompt = dynamic_prompt
        except Exception:
            logger.warning("⚠️ No se pudo actualizar system_prompt dinámico en el agente")
    
    def process_message(self, phone_number: str, message: str) -> str:
        """
        Procesa un mensaje de WhatsApp y devuelve la respuesta.
        
        Args:
            phone_number: Número de WhatsApp del usuario (formato: whatsapp:+34...)
            message: Mensaje de texto del usuario
            
        Returns:
            str: Respuesta del agente
        """
        clean_phone = self._sanitize_phone_number(phone_number)
        
        try:
            logger.info(f"📨 Procesando mensaje de {clean_phone}: {message[:50]}...")
            
            # Obtener o crear agente
            agent = self._get_or_create_agent(phone_number)
            self._refresh_agent_system_prompt(agent)

            # Inyectar metadatos del canal para evitar pedir el teléfono al usuario
            enriched_message = self._build_message_with_metadata(clean_phone, message)

            # Procesar mensaje
            results = agent(enriched_message)
            response = results.message['content'][0]['text']
            response = self._sanitize_agent_response(response)
            
            # Limitar longitud para WhatsApp
            if len(response) > settings.max_message_length:
                logger.warning(f"⚠️ Respuesta muy larga ({len(response)} chars), truncando")
                response = response[:settings.max_message_length - 50] + (
                    "...\n\n(Mensaje completo en próxima respuesta)"
                )
            
            logger.info(f"✅ Respuesta generada para {clean_phone}: {response[:50]}...")
            return response
            
        except Exception as e:
            logger.error(
                f"❌ Error procesando mensaje de {clean_phone}: {e}",
                exc_info=True
            )
            return ERROR_MESSAGES["generic"]
    
    def clear_user_session(self, phone_number: str) -> bool:
        """
        Limpia la sesión de un usuario específico.
        Útil para testing o resetear conversaciones.
        """
        clean_phone = self._sanitize_phone_number(phone_number)
        
        if clean_phone in self.agents:
            del self.agents[clean_phone]
            logger.info(f"🗑️  Sesión eliminada para {clean_phone}")
            return True
        
        logger.warning(f"⚠️ No existe sesión para {clean_phone}")
        return False
    
    def get_active_sessions_count(self) -> int:
        """Obtener número de sesiones activas."""
        return len(self.agents)
    
    def clear_all_sessions(self):
        """Limpiar todas las sesiones (útil para mantenimiento)."""
        count = len(self.agents)
        self.agents.clear()
        logger.info(f"🗑️  {count} sesiones eliminadas")


# Instancia global
agent_manager = RestaurantAgentManager()
