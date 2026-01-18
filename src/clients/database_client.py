import sqlite3
import uuid
import os
from typing import List, Dict, Any, Optional
from src.core.logger import logger
from src.core.exceptions import DatabaseError
from src.core.singleton import SingletonMeta

class DatabaseClient(metaclass=SingletonMeta):
    """
    Cemil Bot için merkezi veritabanı yönetim sınıfı.
    SQLite bağlantı yönetiminden sorumludur.
    """

    def __init__(self, db_path: str = "data/cemil_bot.db"):
        self.db_path = db_path
        # Klasör yoksa oluştur
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.init_db()

    def get_connection(self):
        """SQLite bağlantısı döndürür."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # Dict benzeri erişim için
            return conn
        except sqlite3.Error as e:
            logger.error(f"[X] Veritabanı bağlantı hatası: {e}")
            raise DatabaseError(f"Veritabanına bağlanılamadı: {e}")

    def init_db(self):
        """Temel tabloları hazırlar (Gerekirse)."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Kullanıcılar Tablosu (Users)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        slack_id TEXT UNIQUE,
                        first_name TEXT,
                        middle_name TEXT,
                        surname TEXT,
                        full_name TEXT,
                        birthday TEXT,
                        cohort TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Migration: Gereksiz kolonları kaldır ve sadece gerekli kolonları bırak
                cursor.execute("PRAGMA table_info(users)")
                columns = [column[1] for column in cursor.fetchall()]
                
                required_columns = ['id', 'slack_id', 'first_name', 'middle_name', 'surname', 'full_name', 'birthday', 'cohort', 'created_at', 'updated_at']
                has_unnecessary_columns = any(col not in required_columns for col in columns)
                missing_cohort = 'cohort' not in columns
                missing_middle_name = 'middle_name' not in columns
                has_department = 'department' in columns
                
                # Eğer middle_name kolonu yoksa ekle
                if missing_middle_name:
                    logger.info("[i] middle_name kolonu ekleniyor...")
                    cursor.execute("ALTER TABLE users ADD COLUMN middle_name TEXT")
                    logger.info("[+] middle_name kolonu eklendi.")
                
                if has_unnecessary_columns or missing_cohort or has_department or missing_middle_name:
                    logger.info("[i] Veritabanı şeması güncelleniyor...")
                    # Yeni temiz tablo oluştur
                    cursor.execute("DROP TABLE IF EXISTS users_new")
                    cursor.execute("""
                        CREATE TABLE users_new (
                            id TEXT PRIMARY KEY,
                            slack_id TEXT UNIQUE,
                            first_name TEXT,
                            middle_name TEXT,
                            surname TEXT,
                            full_name TEXT,
                            birthday TEXT,
                            cohort TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    
                    # Mevcut verileri kopyala (sadece mevcut kolonlar varsa)
                    if 'id' in columns and 'slack_id' in columns:
                        # Department varsa cohort'a çevir, yoksa boş bırak
                        if has_department:
                            cursor.execute("""
                                INSERT INTO users_new (id, slack_id, first_name, middle_name, surname, full_name, birthday, cohort, created_at, updated_at)
                                SELECT 
                                    id, 
                                    slack_id, 
                                    COALESCE(first_name, '') as first_name,
                                    COALESCE(middle_name, '') as middle_name,
                                    COALESCE(surname, '') as surname,
                                    COALESCE(full_name, '') as full_name,
                                    birthday,
                                    COALESCE(department, '') as cohort,
                                    COALESCE(created_at, CURRENT_TIMESTAMP) as created_at,
                                    COALESCE(updated_at, CURRENT_TIMESTAMP) as updated_at
                                FROM users
                            """)
                        else:
                            cursor.execute("""
                                INSERT INTO users_new (id, slack_id, first_name, middle_name, surname, full_name, birthday, cohort, created_at, updated_at)
                                SELECT 
                                    id, 
                                    slack_id, 
                                    COALESCE(first_name, '') as first_name,
                                    COALESCE(middle_name, '') as middle_name,
                                    COALESCE(surname, '') as surname,
                                    COALESCE(full_name, '') as full_name,
                                    birthday,
                                    COALESCE(cohort, '') as cohort,
                                    COALESCE(created_at, CURRENT_TIMESTAMP) as created_at,
                                    COALESCE(updated_at, CURRENT_TIMESTAMP) as updated_at
                                FROM users
                            """)
                    
                    # Eski tabloyu sil ve yenisini yeniden adlandır
                    cursor.execute("DROP TABLE users")
                    cursor.execute("ALTER TABLE users_new RENAME TO users")
                    logger.info("[+] Veritabanı şeması temizlendi: Sadece gerekli kolonlar kaldı (id, slack_id, first_name, middle_name, surname, full_name, birthday, cohort).")

                # Eşleşme Takip Tablosu (Matches)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS matches (
                        id TEXT PRIMARY KEY,
                        channel_id TEXT,
                        coffee_channel_id TEXT,
                        user1_id TEXT,
                        user2_id TEXT,
                        status TEXT DEFAULT 'active',
                        summary TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Migration: coffee_channel_id kolonu yoksa ekle
                cursor.execute("PRAGMA table_info(matches)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'coffee_channel_id' not in columns:
                    logger.info("[i] coffee_channel_id kolonu ekleniyor...")
                    cursor.execute("ALTER TABLE matches ADD COLUMN coffee_channel_id TEXT")
                    logger.info("[+] coffee_channel_id kolonu eklendi.")

                # Oylama Başlıkları Tablosu (Polls)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS polls (
                        id TEXT PRIMARY KEY,
                        topic TEXT,
                        options TEXT, -- JSON formatında seçenekler
                        result_summary TEXT, -- Oylama bittiğinde LLM özeti veya ham sonuç
                        creator_id TEXT,
                        allow_multiple INTEGER DEFAULT 0, -- Çoklu oy opsiyonu
                        is_closed INTEGER DEFAULT 0,
                        expires_at TIMESTAMP,
                        message_ts TEXT, -- Oylama mesajının timestamp'i (güncelleme için)
                        message_channel TEXT, -- Oylama mesajının kanalı
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Migration: Eğer message_ts ve message_channel kolonları yoksa ekle
                cursor.execute("PRAGMA table_info(polls)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'message_ts' not in columns:
                    cursor.execute("ALTER TABLE polls ADD COLUMN message_ts TEXT")
                    logger.info("[i] polls tablosuna message_ts kolonu eklendi.")
                if 'message_channel' not in columns:
                    cursor.execute("ALTER TABLE polls ADD COLUMN message_channel TEXT")
                    logger.info("[i] polls tablosuna message_channel kolonu eklendi.")

                # Oylar Tablosu (Votes) - User & Poll Ara Tablo
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS votes (
                        id TEXT PRIMARY KEY,
                        poll_id TEXT,
                        user_id TEXT,
                        option_index INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(poll_id, user_id, option_index)
                    )
                """)

                # Anonim Geri Bildirim Tablosu (Feedbacks)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS feedbacks (
                        id TEXT PRIMARY KEY,
                        content TEXT,
                        category TEXT DEFAULT 'general',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Yardım İstekleri Tablosu (Help Requests)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS help_requests (
                        id TEXT PRIMARY KEY,
                        requester_id TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        description TEXT NOT NULL,
                        status TEXT DEFAULT 'open',
                        helper_id TEXT,
                        channel_id TEXT,
                        help_channel_id TEXT,
                        message_ts TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        resolved_at TIMESTAMP
                    )
                """)
                
                # Migration: help_channel_id kolonu yoksa ekle
                cursor.execute("PRAGMA table_info(help_requests)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'help_channel_id' not in columns:
                    logger.info("[i] help_channel_id kolonu ekleniyor...")
                    cursor.execute("ALTER TABLE help_requests ADD COLUMN help_channel_id TEXT")
                    logger.info("[+] help_channel_id kolonu eklendi.")
                
                # Challenge Hub Tabloları
                # Challenge Themes (Temalar)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS challenge_themes (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        description TEXT,
                        icon TEXT,
                        difficulty_range TEXT,
                        is_active INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Challenge Projects (Proje Şablonları)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS challenge_projects (
                        id TEXT PRIMARY KEY,
                        theme TEXT NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT,
                        objectives TEXT,
                        deliverables TEXT,
                        tasks TEXT,
                        difficulty_level TEXT DEFAULT 'intermediate',
                        estimated_hours INTEGER DEFAULT 48,
                        min_team_size INTEGER DEFAULT 2,
                        max_team_size INTEGER DEFAULT 6,
                        learning_objectives TEXT,
                        skills_required TEXT,
                        skills_developed TEXT,
                        resources TEXT,
                        knowledge_base_refs TEXT,
                        llm_customizable INTEGER DEFAULT 1,
                        llm_enhancement_prompt TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Challenge Hubs (Ana Challenge'lar)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS challenge_hubs (
                        id TEXT PRIMARY KEY,
                        creator_id TEXT NOT NULL,
                        theme TEXT NOT NULL,
                        team_size INTEGER NOT NULL,
                        status TEXT DEFAULT 'recruiting',
                        challenge_channel_id TEXT,
                        hub_channel_id TEXT,
                        selected_project_id TEXT,
                        llm_customizations TEXT,
                        deadline_hours INTEGER DEFAULT 48,
                        difficulty TEXT DEFAULT 'intermediate',
                        deadline TIMESTAMP,
                        started_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Challenge Participants (Katılımcılar)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS challenge_participants (
                        id TEXT PRIMARY KEY,
                        challenge_hub_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        role TEXT,
                        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        points_earned INTEGER DEFAULT 0,
                        UNIQUE(challenge_hub_id, user_id)
                    )
                """)
                
                # Challenge Submissions (Takım Çıktıları)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS challenge_submissions (
                        id TEXT PRIMARY KEY,
                        challenge_hub_id TEXT NOT NULL,
                        team_name TEXT,
                        project_name TEXT,
                        solution_summary TEXT,
                        deliverables TEXT,
                        learning_outcomes TEXT,
                        llm_enhanced_features TEXT,
                        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        points_awarded INTEGER DEFAULT 0,
                        creativity_score INTEGER DEFAULT 0,
                        teamwork_score INTEGER DEFAULT 0
                    )
                """)
                
                # User Challenge Stats (Kullanıcı İstatistikleri)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_challenge_stats (
                        user_id TEXT PRIMARY KEY,
                        total_challenges INTEGER DEFAULT 0,
                        completed_challenges INTEGER DEFAULT 0,
                        total_points INTEGER DEFAULT 0,
                        creativity_points INTEGER DEFAULT 0,
                        teamwork_points INTEGER DEFAULT 0,
                        favorite_theme TEXT,
                        last_challenge_date DATE,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                conn.commit()
                logger.debug("[i] Veritabanı tabloları kontrol edildi.")
                
                # Seed data: Temalar ve Projeler
                self._seed_challenge_data(cursor)
                conn.commit()
                
        except sqlite3.Error as e:
            logger.error(f"[X] Veritabanı ilklendirme hatası: {e}")
            raise DatabaseError(f"Tablolar oluşturulamadı: {e}")
    
    def _seed_challenge_data(self, cursor):
        """Challenge temaları ve projeler için seed data ekler."""
        try:
            # Temalar
            themes = [
                ("theme_ai_chatbot", "AI Chatbot", "Yapay zeka destekli chatbot geliştirme", "🤖", "intermediate-advanced", 1),
                ("theme_web_app", "Web App", "Modern web uygulaması geliştirme", "🌐", "intermediate-advanced", 1),
                ("theme_data_analysis", "Data Analysis", "Veri analizi ve görselleştirme projeleri", "📊", "intermediate", 1),
                ("theme_mobile_app", "Mobile App", "Mobil uygulama geliştirme", "📱", "advanced", 1),
                ("theme_automation", "Automation", "İş süreçlerini otomatikleştirme", "⚙️", "intermediate", 1),
            ]
            
            for theme_id, name, desc, icon, diff_range, is_active in themes:
                cursor.execute("""
                    INSERT OR IGNORE INTO challenge_themes (id, name, description, icon, difficulty_range, is_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (theme_id, name, desc, icon, diff_range, is_active))
            
            # Projeler (AI Chatbot)
            import json
            ai_chatbot_projects = [
                {
                    "id": "proj_edu_assistant",
                    "theme": "AI Chatbot",
                    "name": "Eğitim Asistanı Chatbot",
                    "description": "Öğrencilerin ders planı çıkaran, soru cevaplayan ve öğrenme yolculuğunu destekleyen akıllı chatbot sistemi.",
                    "objectives": json.dumps(["Prompt tasarımı", "Akış diyagramı", "Örnek konuşmalar", "Sunum"]),
                    "deliverables": json.dumps(["prompt", "flow_diagram", "demo_conversations", "presentation"]),
                    "tasks": json.dumps([
                        {"title": "Prompt Tasarımı", "description": "Chatbot'un temel prompt'unu tasarla", "estimated_hours": 8},
                        {"title": "Akış Diyagramı", "description": "Kullanıcı etkileşim akışını tasarla", "estimated_hours": 6},
                        {"title": "Örnek Konuşmalar", "description": "3 farklı senaryo için örnek diyaloglar", "estimated_hours": 6},
                        {"title": "Sunum", "description": "Proje sunumu hazırla", "estimated_hours": 4}
                    ]),
                    "difficulty_level": "intermediate",
                    "estimated_hours": 48,
                    "min_team_size": 2,
                    "max_team_size": 6
                },
                {
                    "id": "proj_customer_support",
                    "theme": "AI Chatbot",
                    "name": "Müşteri Destek Botu",
                    "description": "E-ticaret sitesi için müşteri sorularını yanıtlayan bot",
                    "objectives": json.dumps(["FAQ entegrasyonu", "Ticket yönlendirme", "Kişiselleştirilmiş yanıtlar"]),
                    "deliverables": json.dumps(["faq_database", "routing_flow", "sample_dialogues"]),
                    "tasks": json.dumps([
                        {"title": "FAQ Veritabanı", "description": "FAQ şeması ve içerik", "estimated_hours": 8},
                        {"title": "Yönlendirme Akışı", "description": "Ticket yönlendirme mantığı", "estimated_hours": 6},
                        {"title": "Örnek Diyaloglar", "description": "Farklı senaryolar için diyaloglar", "estimated_hours": 6}
                    ]),
                    "difficulty_level": "intermediate",
                    "estimated_hours": 48,
                    "min_team_size": 2,
                    "max_team_size": 5
                }
            ]
            
            for project in ai_chatbot_projects:
                cursor.execute("""
                    INSERT OR IGNORE INTO challenge_projects 
                    (id, theme, name, description, objectives, deliverables, tasks, difficulty_level, estimated_hours, min_team_size, max_team_size)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    project["id"],
                    project["theme"],
                    project["name"],
                    project["description"],
                    project["objectives"],
                    project["deliverables"],
                    project["tasks"],
                    project["difficulty_level"],
                    project["estimated_hours"],
                    project["min_team_size"],
                    project["max_team_size"]
                ))
            
            logger.info("[+] Challenge seed data eklendi.")
        except Exception as e:
            logger.warning(f"[!] Challenge seed data eklenirken hata: {e}")