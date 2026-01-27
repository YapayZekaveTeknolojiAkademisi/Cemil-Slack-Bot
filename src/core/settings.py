"""
Cemil Bot için merkezi konfigürasyon yönetimi.
Pydantic Settings kullanarak environment variable'ları yönetir.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, ConfigDict


class BotSettings(BaseSettings):
    """Bot ayarları - Environment variable'lardan yüklenir."""
    
    # Slack Ayarları
    slack_bot_token: str = Field(..., description="Slack Bot Token (xoxb-...)")
    slack_app_token: str = Field(..., description="Slack App Token (xapp-...)")
    slack_user_token: Optional[str] = Field(None, description="Slack User Token (xoxp-...) - Kanal oluşturma için")
    
    # Groq AI Ayarları
    groq_api_key: str = Field(..., description="Groq API Key")
    
    # SMTP Ayarları (Opsiyonel)
    smtp_email: Optional[str] = Field(None, description="SMTP Email adresi")
    smtp_password: Optional[str] = Field(None, description="SMTP Password")
    
    # Slack Kanal Ayarları
    admin_channel_id: Optional[str] = Field(None, description="Admin kanalı ID")
    startup_channel: Optional[str] = Field(
        None, 
        description="Başlangıç mesajı kanalı",
        validation_alias="SLACK_STARTUP_CHANNEL"
    )
    allowed_command_channels: Optional[str] = Field(
        None,
        description="Virgülle ayrılmış izinli komut kanal ID listesi (örn: C123,C456)"
    )
    
    # GitHub Repo (Opsiyonel)
    github_repo: Optional[str] = Field(None, description="GitHub repository URL")
    
    # Admin Ayarları
    admin_slack_id: Optional[str] = Field(None, description="Admin kullanıcı Slack ID")
    
    # Logging Ayarları
    log_level: str = Field("INFO", description="Log seviyesi (DEBUG, INFO, WARNING, ERROR)")
    log_file: str = Field("logs/cemil_detailed.log", description="Log dosyası yolu")
    
    # Rate Limiting Ayarları
    rate_limit_requests: int = Field(10, description="Rate limit - dakikada maksimum istek")
    rate_limit_window: int = Field(60, description="Rate limit - zaman penceresi (saniye)")
    
    # Vector Store Ayarları
    vector_store_path: str = Field("data/vector_store.index", description="Vector store dosya yolu")
    vector_store_pkl_path: str = Field("data/vector_store.pkl", description="Vector store pickle dosya yolu")
    
    # Database Ayarları
    database_path: str = Field(
        "data/cemil_bot.db", 
        description="SQLite veritabanı yolu",
        validation_alias="DB_PATH"
    )
    
    # Knowledge Base Ayarları
    knowledge_base_path: str = Field("knowledge_base", description="Bilgi küpü klasör yolu")
    
    # Başlangıç Senaryo Ayarları (Soruları Otomatize Etmek İçin)
    db_clean_on_startup: bool = Field(False, description="Başlangıçta challenge tablolarını temizle")
    db_import_initial_users: bool = Field(False, description="Başlangıçta data/initial_users.csv dosyasını içe aktar")
    kb_rebuild_index: bool = Field(False, description="Başlangıçta bilgi küpü vektör indeksini yeniden oluştur")
    slack_send_welcome_message: bool = Field(False, description="Sanal ortamda hoşgeldin mesajı gönder")
    
    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Log seviyesini doğrula."""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f"Log seviyesi {valid_levels} arasından biri olmalı")
        return v.upper()
    
    @field_validator('rate_limit_requests', 'rate_limit_window')
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        """Pozitif integer doğrula."""
        if v <= 0:
            raise ValueError("Değer pozitif olmalı")
        return v
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"  # Bilinmeyen environment variable'ları yoksay
    )


# Global settings instance
_settings: Optional[BotSettings] = None


def get_settings(reload: bool = False) -> BotSettings:
    """Settings singleton instance döndürür."""
    global _settings
    if _settings is None or reload:
        _settings = BotSettings()
    return _settings
