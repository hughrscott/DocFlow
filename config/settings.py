"""
Settings and configuration management for DocFlow backend.

Loads configuration from environment variables and config files.
Uses Pydantic for validation and type safety.
"""

from pydantic_settings import BaseSettings
from typing import List, Optional
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # FastAPI Settings
    fastapi_env: str = os.getenv("FASTAPI_ENV", "development")
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Security
    secret_key: str = os.getenv("SECRET_KEY", "change-this-in-production")
    algorithm: str = os.getenv("ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    
    # Authentication
    auth_enabled: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    default_username: str = os.getenv("DEFAULT_USERNAME", "admin")
    default_password: str = os.getenv("DEFAULT_PASSWORD", "changeme")
    
    # File Storage
    documents_dir: str = os.getenv("DOCUMENTS_DIR", "./documents")
    uploads_temp_dir: str = os.getenv("UPLOADS_TEMP_DIR", "./uploads_temp")
    max_upload_size_mb: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "100"))
    
    # Database
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./docflow.db")
    
    # LLM Configuration
    llm_config_path: str = os.getenv("LLM_CONFIG_PATH", "./config/llm_config.yaml")
    
    # Claude API
    claude_api_key: Optional[str] = os.getenv("CLAUDE_API_KEY", None)
    
    # Ollama
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    
    # API Settings
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]
    
    # Processing
    max_workers: int = int(os.getenv("MAX_WORKERS", "4"))
    pdf_processing_timeout: int = int(os.getenv("PDF_PROCESSING_TIMEOUT", "300"))
    llm_request_timeout: int = int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))
    
    # Learning
    learning_enabled: bool = os.getenv("LEARNING_ENABLED", "true").lower() == "true"
    min_confidence_threshold: float = float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.7"))
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()
