"""
Input validation için Pydantic modelleri.
"""

import re
from typing import List, Optional
from pydantic import BaseModel, field_validator, Field


class PollRequest(BaseModel):
    """Oylama komutu için input validation."""
    
    minutes: int = Field(..., description="Oylama süresi (dakika)")
    topic: str = Field(..., description="Oylama konusu")
    options: List[str] = Field(..., description="Oylama seçenekleri")
    
    @field_validator('minutes')
    @classmethod
    def validate_minutes(cls, v: int) -> int:
        """Dakika değerini doğrula."""
        if not 1 <= v <= 1440:  # 1 dakika - 24 saat
            raise ValueError('Oylama süresi 1-1440 dakika (24 saat) arasında olmalı')
        return v
    
    @field_validator('topic')
    @classmethod
    def validate_topic(cls, v: str) -> str:
        """Konu başlığını doğrula."""
        v = v.strip()
        if not v:
            raise ValueError('Konu başlığı boş olamaz')
        if len(v) > 200:
            raise ValueError('Konu başlığı en fazla 200 karakter olabilir')
        return v
    
    @field_validator('options')
    @classmethod
    def validate_options(cls, v: List[str]) -> List[str]:
        """Seçenekleri doğrula."""
        if len(v) < 2:
            raise ValueError('En az 2 seçenek gerekli')
        if len(v) > 10:
            raise ValueError('En fazla 10 seçenek olabilir')
        
        # Seçenekleri temizle ve doğrula
        cleaned_options = []
        for opt in v:
            opt = opt.strip()
            if not opt:
                raise ValueError('Boş seçenek olamaz')
            if len(opt) > 100:
                raise ValueError('Her seçenek en fazla 100 karakter olabilir')
            cleaned_options.append(opt)
        
        return cleaned_options
    
    @classmethod
    def parse_from_text(cls, text: str) -> 'PollRequest':
        """
        /oylama komutundan gelen text'i parse eder.
        Format: /oylama [Dakika] [Konu] | Seçenek 1 | Seçenek 2 | ...
        """
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Eksik parametre. Format: /oylama [Dakika] [Konu] | Seçenek 1 | Seçenek 2")
        
        try:
            minutes = int(parts[0])
        except ValueError:
            raise ValueError("İlk parametre bir sayı olmalı (dakika)")
        
        content_parts = parts[1].split("|")
        if len(content_parts) < 3:
            raise ValueError("En az iki seçenek gerekli. Format: [Konu] | Seçenek 1 | Seçenek 2")
        
        topic = content_parts[0].strip()
        options = [opt.strip() for opt in content_parts[1:]]
        
        return cls(minutes=minutes, topic=topic, options=options)


class FeedbackRequest(BaseModel):
    """Geri bildirim komutu için input validation."""
    
    category: str = Field(default="general", description="Geri bildirim kategorisi")
    content: str = Field(..., description="Geri bildirim içeriği")
    
    @field_validator('content')
    @classmethod
    def validate_content(cls, v: str) -> str:
        """İçeriği doğrula."""
        v = v.strip()
        if not v:
            raise ValueError('Geri bildirim içeriği boş olamaz')
        if len(v) > 2000:
            raise ValueError('Geri bildirim en fazla 2000 karakter olabilir')
        return v
    
    @field_validator('category')
    @classmethod
    def validate_category(cls, v: str) -> str:
        """Kategoriyi doğrula."""
        v = v.strip().lower()
        valid_categories = ['general', 'technical', 'feature', 'bug', 'other']
        if v not in valid_categories:
            return 'general'  # Geçersiz kategori için default
        return v
    
    @classmethod
    def parse_from_text(cls, text: str) -> 'FeedbackRequest':
        """
        /geri-bildirim komutundan gelen text'i parse eder.
        Format: /geri-bildirim [kategori] [mesaj]
        """
        if not text:
            raise ValueError("Geri bildirim içeriği gerekli")
        
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            # Sadece içerik var, kategori yok
            return cls(content=parts[0])
        else:
            # Kategori ve içerik var
            return cls(category=parts[0], content=parts[1])


class QuestionRequest(BaseModel):
    """Soru komutu için input validation."""
    
    question: str = Field(..., description="Sorulan soru")
    
    @field_validator('question')
    @classmethod
    def validate_question(cls, v: str) -> str:
        """Soruyu doğrula."""
        v = v.strip()
        if not v:
            raise ValueError('Soru boş olamaz')
        if len(v) > 500:
            raise ValueError('Soru en fazla 500 karakter olabilir')
        return v


class HelpRequest(BaseModel):
    """Yardım isteği komutu için input validation."""
    
    topic: str = Field(..., description="Yardım isteği konusu")
    description: str = Field(default="", description="Detaylı açıklama")
    
    @field_validator('topic')
    @classmethod
    def validate_topic(cls, v: str) -> str:
        """Konuyu doğrula."""
        v = v.strip()
        if not v:
            raise ValueError('Yardım isteği konusu boş olamaz')
        if len(v) > 200:
            raise ValueError('Konu en fazla 200 karakter olabilir')
        return v
    
    @field_validator('description')
    @classmethod
    def validate_description(cls, v: str) -> str:
        """Açıklamayı doğrula."""
        v = v.strip()
        if len(v) > 1000:
            raise ValueError('Açıklama en fazla 1000 karakter olabilir')
        return v
    
    @classmethod
    def parse_from_text(cls, text: str) -> 'HelpRequest':
        """
        /yardim-iste komutundan gelen text'i parse eder.
        Format: /yardim-iste [konu] [açıklama]
        """
        if not text:
            raise ValueError("Yardım isteği için en azından konu gerekli")
        
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            # Sadece konu var
            return cls(topic=parts[0], description="Detaylı açıklama eklenmedi.")
        else:
            # Konu ve açıklama var
            return cls(topic=parts[0], description=parts[1])


class ChallengeStartRequest(BaseModel):
    """Challenge başlatma komutu için input validation."""
    
    team_size: int = Field(
        ...,
        ge=2,
        le=6,
        description="Takım büyüklüğü (2-6 arası)"
    )
    theme: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Challenge teması"
    )
    deadline_hours: Optional[int] = Field(
        48,
        ge=12,
        le=168,
        description="Süre (12-168 saat)"
    )
    difficulty: Optional[str] = Field(
        "intermediate",
        description="Zorluk seviyesi"
    )
    
    @field_validator('theme')
    @classmethod
    def validate_theme(cls, v: str) -> str:
        """Temayı doğrula."""
        valid_themes = [
            "AI Chatbot",
            "Web App",
            "Data Analysis",
            "Mobile App",
            "Automation"
        ]
        v = v.strip()
        if v not in valid_themes:
            raise ValueError(
                f"Geçersiz tema: '{v}'. "
                f"Geçerli temalar: {', '.join(valid_themes)}"
            )
        return v
    
    @field_validator('difficulty')
    @classmethod
    def validate_difficulty(cls, v: str) -> str:
        """Zorluk seviyesini doğrula."""
        valid_difficulties = ["beginner", "intermediate", "advanced"]
        if v not in valid_difficulties:
            raise ValueError(
                f"Geçersiz zorluk: '{v}'. "
                f"Geçerli seviyeler: {', '.join(valid_difficulties)}"
            )
        return v
    
    @classmethod
    def parse_from_text(cls, text: str) -> 'ChallengeStartRequest':
        """
        Parse: /challenge start 4 "AI Chatbot" 48 "advanced"
        """
        text = text.strip()
        
        # Tırnak içindeki theme'i bul
        theme_match = re.search(r'["\']([^"\']+)["\']', text)
        if not theme_match:
            raise ValueError("Tema tırnak içinde olmalı. Örnek: \"AI Chatbot\"")
        
        theme = theme_match.group(1)
        
        # Tırnaklı kısmı çıkar
        text_without_theme = re.sub(r'["\'][^"\']+["\']', '', text)
        parts = [p for p in text_without_theme.split() if p]
        
        if not parts:
            raise ValueError("team_size gerekli")
        
        # team_size (ilk parametre)
        try:
            team_size = int(parts[0])
        except ValueError:
            raise ValueError("team_size bir sayı olmalı")
        
        # deadline_hours (opsiyonel)
        deadline_hours = 48
        if len(parts) > 1 and parts[1].isdigit():
            deadline_hours = int(parts[1])
            parts = parts[2:]
        else:
            parts = parts[1:]
        
        # difficulty (opsiyonel)
        difficulty = "intermediate"
        if parts:
            difficulty = parts[0]
        
        return cls(
            team_size=team_size,
            theme=theme,
            deadline_hours=deadline_hours,
            difficulty=difficulty
        )


class ChallengeJoinRequest(BaseModel):
    """Challenge katılma komutu için input validation."""
    
    challenge_id: Optional[str] = Field(
        None,
        description="Challenge ID (opsiyonel, boşsa aktif challenge'a katılır)"
    )
    
    @classmethod
    def parse_from_text(cls, text: str) -> 'ChallengeJoinRequest':
        """
        Parse: /challenge join [challenge_id]
        """
        text = text.strip()
        if not text:
            return cls(challenge_id=None)
        return cls(challenge_id=text)

