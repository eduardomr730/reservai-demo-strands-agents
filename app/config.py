"""
Configuración centralizada de la aplicación.
"""
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from functools import lru_cache
from typing import Literal


class Settings(BaseSettings):
    """Configuración de la aplicación."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Entorno
    environment: Literal["development", "production", "test"] = "production"
    debug: bool = False
    
    # Server
    port: int = 8000
    host: str = "0.0.0.0"
    workers: int = 2
    
    # Twilio
    twilio_auth_token: str
    twilio_account_sid: str = ""  # Opcional para validación
    validate_twilio: bool = True
    
    # AWS
    aws_region: str = "eu-west-1"
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str = ""
    
    # Agente
    agentcore_memory_id: str
    agent_model: str = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
    
    # DynamoDB
    dynamodb_table_name: str = "restaurant-reservations"
    dynamodb_region: str = "eu-west-1"
    
    # Logging
    log_level: str = "INFO"
    
    # Límites
    max_message_length: int = 1600
    max_reservations_per_query: int = 50
    

@lru_cache()
def get_settings() -> Settings:
    """Obtener configuración (cacheada)."""
    return Settings()


# Para importar fácilmente
settings = get_settings()
