#!/usr/bin/env python3
"""
Cemil Bot - Topluluk Etkileşim Asistanı
Ana bot dosyası: Tüm servislerin entegrasyonu ve slash komutları
"""

import os
import asyncio
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# --- Core & Clients ---
from src.core.logger import logger
from src.core.settings import get_settings
from src.clients import (
    DatabaseClient,
    GroqClient,
    CronClient,
    VectorClient,
    SMTPClient
)

# --- Commands (Slack API Wrappers) ---
from src.commands import (
    ChatManager,
    ConversationManager,
    UserManager
)

# --- Repositories ---
from src.repositories import (
    UserRepository,
    MatchRepository,
    PollRepository,
    VoteRepository,
    FeedbackRepository,
    HelpRepository,
    ChallengeHubRepository,
    ChallengeParticipantRepository,
    ChallengeProjectRepository,
    ChallengeSubmissionRepository,
    ChallengeThemeRepository,
    UserChallengeStatsRepository
)

# --- Services ---
from src.services import (
    CoffeeMatchService,
    VotingService,
    FeedbackService,
    KnowledgeService,
    HelpService,
    StatisticsService,
    ChallengeEnhancementService,
    ChallengeHubService
)

# --- Handlers ---
from src.handlers import (
    setup_coffee_handlers,
    setup_poll_handlers,
    setup_feedback_handlers,
    setup_knowledge_handlers,
    setup_profile_handlers,
    setup_health_handlers,
    setup_help_handlers,
    setup_statistics_handlers,
    setup_challenge_handlers
)

# ============================================================================
# KONFIGÜRASYON
# ============================================================================

load_dotenv()
settings = get_settings()

# Slack App Başlatma - Token kontrolü
if not settings.slack_bot_token:
    raise ValueError("SLACK_BOT_TOKEN environment variable is required!")

app = App(token=settings.slack_bot_token)

# ============================================================================
# CLIENT İLKLENDİRME (Singleton Pattern)
# ============================================================================

logger.info("[i] Client'lar ilklendiriliyor...")
db_client = DatabaseClient(db_path=settings.database_path)
groq_client = GroqClient()
cron_client = CronClient()
vector_client = VectorClient()
smtp_client = SMTPClient()
logger.info("[+] Client'lar hazır.")

# ============================================================================
# COMMAND MANAGER İLKLENDİRME
# ============================================================================

logger.info("[i] Command Manager'lar ilklendiriliyor...")
chat_manager = ChatManager(app.client)
conv_manager = ConversationManager(app.client)
user_manager = UserManager(app.client)
logger.info("[+] Command Manager'lar hazır.")

# ============================================================================
# REPOSITORY İLKLENDİRME
# ============================================================================

logger.info("[i] Repository'ler ilklendiriliyor...")
user_repo = UserRepository(db_client)
match_repo = MatchRepository(db_client)
poll_repo = PollRepository(db_client)
vote_repo = VoteRepository(db_client)
feedback_repo = FeedbackRepository(db_client)
help_repo = HelpRepository(db_client)
challenge_hub_repo = ChallengeHubRepository(db_client)
challenge_participant_repo = ChallengeParticipantRepository(db_client)
challenge_project_repo = ChallengeProjectRepository(db_client)
challenge_submission_repo = ChallengeSubmissionRepository(db_client)
challenge_theme_repo = ChallengeThemeRepository(db_client)
user_challenge_stats_repo = UserChallengeStatsRepository(db_client)
logger.info("[+] Repository'ler hazır.")

# ============================================================================
# SERVİS İLKLENDİRME
# ============================================================================

logger.info("[i] Servisler ilklendiriliyor...")
coffee_service = CoffeeMatchService(
    chat_manager, conv_manager, groq_client, cron_client, match_repo
)
voting_service = VotingService(
    chat_manager, poll_repo, vote_repo, cron_client
)
feedback_service = FeedbackService(
    chat_manager, smtp_client, feedback_repo
)
knowledge_service = KnowledgeService(
    vector_client, groq_client
)
help_service = HelpService(
    chat_manager, conv_manager, user_manager, help_repo, user_repo, groq_client, cron_client
)
statistics_service = StatisticsService(
    user_repo, match_repo, help_repo, feedback_repo, poll_repo, vote_repo
)
challenge_enhancement_service = ChallengeEnhancementService(
    groq_client, knowledge_service
)
challenge_hub_service = ChallengeHubService(
    chat_manager, conv_manager, user_manager,
    challenge_hub_repo, challenge_participant_repo,
    challenge_project_repo, challenge_submission_repo,
    challenge_theme_repo, user_challenge_stats_repo,
    challenge_enhancement_service, groq_client, cron_client
)
logger.info("[+] Servisler hazır.")

# ============================================================================
# HANDLER KAYITLARI
# ============================================================================

logger.info("[i] Handler'lar kaydediliyor...")
setup_coffee_handlers(app, coffee_service, chat_manager, user_repo)
setup_poll_handlers(app, voting_service, chat_manager, user_repo)
setup_feedback_handlers(app, feedback_service, chat_manager, user_repo)
setup_knowledge_handlers(app, knowledge_service, chat_manager, user_repo)
setup_profile_handlers(app, chat_manager, user_repo)
setup_health_handlers(app, chat_manager, db_client, groq_client, vector_client)
setup_help_handlers(app, help_service, chat_manager, user_repo)
setup_statistics_handlers(app, statistics_service, chat_manager, user_repo)
setup_challenge_handlers(app, challenge_hub_service, chat_manager, user_repo)
logger.info("[+] Handler'lar kaydedildi.")

# ============================================================================
# GLOBAL HATA YÖNETİMİ
# ============================================================================

@app.error
def global_error_handler(error, body, logger):
    """Tüm beklenmedik hataları yakalar ve loglar."""
    user_id = body.get("user", {}).get("id") or body.get("user_id", "Bilinmiyor")
    channel_id = body.get("channel", {}).get("id") or body.get("channel_id")
    trigger = body.get("command") or body.get("action_id") or "N/A"
    
    logger.error(f"[X] GLOBAL HATA - Kullanıcı: {user_id} - Tetikleyici: {trigger} - Hata: {error}", exc_info=True)
    
    # Kullanıcıya bilgi ver (Eğer kanal bilgisi varsa)
    if channel_id and user_id != "Bilinmiyor":
        try:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="Şu an küçük bir teknik aksaklık yaşıyorum, biraz başım döndü. 🤕 Lütfen birkaç dakika sonra tekrar dener misin?"
            )
        except Exception:
            pass # Hata mesajı gönderirken hata oluşursa yut

# ============================================================================
# BOT BAŞLATMA
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("           CEMIL BOT - BAŞLATMA SIRASI")
    print("="*60 + "\n")
    
    # 1. Veritabanı İlklendirme
    logger.info("[>] Veritabanı kontrol ediliyor...")
    db_client.init_db()

    # --- CSV Veri İçe Aktarma Kontrolü ---
    import sys
    
    # Klasörlerin varlığını kontrol et
    os.makedirs("data", exist_ok=True)
    os.makedirs(settings.knowledge_base_path, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    CSV_PATH = "data/initial_users.csv"
    
    if not os.path.exists(CSV_PATH):
        # Şablon dosya oluştur
        print(f"\n[i] '{CSV_PATH}' dosyası bulunamadı. Şablon oluşturuluyor...")
        try:
            with open(CSV_PATH, 'w', encoding='utf-8') as f:
                f.write("Slack ID,First Name,Surname,Full Name,Birthday,Cohort\n")
                f.write("U12345,Ahmet,Yilmaz,Ahmet Yilmaz,01.01.1990,Yapay Zeka\n")
            print(f"[+] Şablon oluşturuldu: {CSV_PATH}")
            print(f"[i] Not: Şablon içinde örnek veri bulunmaktadır.")
            choice = input("Bu şablonu şimdi kullanmak ister misiniz? (e/h): ").lower().strip()
            
            if choice == 'e':
                print("[i] Veriler işleniyor...")
                try:
                    count = user_repo.import_from_csv(CSV_PATH)
                    print(f"[+] Başarılı! {count} kullanıcı eklendi.")
                except Exception as e:
                    logger.error(f"[X] Import hatası: {e}", exc_info=True)
                    print("Hata oluştu, logları kontrol edin.")
            else:
                print("[i] Şablon atlandı. Dosyayı doldurup botu yeniden başlattığınızda kullanabilirsiniz.")
        except Exception as e:
            logger.error(f"Şablon oluşturma hatası: {e}", exc_info=True)
    else:
        # Dosya var, kullanıp kullanmayacağını sor
        print(f"\n[?] '{CSV_PATH}' dosyası bulundu.")
        choice = input("Bu CSV dosyasındaki verileri kullanmak ister misiniz? (e/h): ").lower().strip()
        
        if choice == 'e':
            print("[i] Veriler işleniyor...")
            try:
                count = user_repo.import_from_csv(CSV_PATH)
                print(f"[+] Başarılı! {count} kullanıcı eklendi.")
            except Exception as e:
                logger.error(f"[X] Import hatası: {e}", exc_info=True)
                print("Hata oluştu, logları kontrol edin.")
        else:
            print("[i] CSV dosyası atlandı, mevcut veritabanı ile devam ediliyor.")
    # -------------------------------------
    
    # 2. Cron Başlatma
    logger.info("[>] Zamanlayıcı başlatılıyor...")
    cron_client.start()
    
    # 3. Vektör Veritabanı Kontrolü
    vector_index_exists = os.path.exists(settings.vector_store_path) and os.path.exists(settings.vector_store_pkl_path)
    
    if vector_index_exists:
        # Mevcut veriler var
        print(f"\n[?] Vektör veritabanı bulundu (mevcut veriler: {len(vector_client.documents) if vector_client.documents else 0} parça).")
        choice = input("Vektör veritabanını yeniden oluşturmak ister misiniz? (e/h): ").lower().strip()
        
        if choice == 'e':
            print("[i] Vektör veritabanı yeniden oluşturuluyor...")
            logger.info("[>] Bilgi Küpü indeksleniyor...")
            asyncio.run(knowledge_service.process_knowledge_base())
            print("[+] Vektör veritabanı başarıyla güncellendi.")
        else:
            print("[i] Mevcut vektör veritabanı kullanılıyor.")
            logger.info("[i] Mevcut vektör veritabanı yüklendi.")
    else:
        # Vektör veritabanı yok, oluştur
        print(f"\n[i] Vektör veritabanı bulunamadı. Oluşturuluyor...")
        logger.info("[>] Bilgi Küpü indeksleniyor...")
        asyncio.run(knowledge_service.process_knowledge_base())
        print("[+] Vektör veritabanı başarıyla oluşturuldu.")
    
    # 5. Slack Socket Mode Başlatma
    if not settings.slack_app_token:
        logger.error("[X] SLACK_APP_TOKEN bulunamadı!")
        exit(1)
    
    logger.info("[>] Slack Socket Mode başlatılıyor...")
    
    # Başlangıç Mesajı Kontrolü
    if settings.startup_channel:
        print(f"\n[?] Başlangıç kanalı bulundu: {settings.startup_channel}")
        choice = input("Başlangıç mesajı (welcome) gönderilsin mi? (e/h): ").lower().strip()
        
        if choice == 'e':
            try:
                startup_text = (
                    "👋 *Merhabalar! Ben Cemil, göreve hazırım!* ☀️\n\n"
                    "Topluluk etkileşimini artırmak için buradayım. İşte güncel yeteneklerim:\n\n"
                    "☕ *`/kahve`* - Kahve molası eşleşmesi için havuza katıl.\n"
                    "🗳️ *`/oylama`* - Hızlı anketler başlat (Admin).\n"
                    "📝 *`/geri-bildirim`* - Yönetime anonim mesaj gönder.\n"
                    "🧠 *`/sor`* - Dökümanlara ve bilgi küpüne soru sor.\n"
                    "🆘 *`/yardim-iste`* - Topluluktan yardım iste.\n"
                    "👤 *`/profilim`* - Kayıtlı bilgilerini görüntüle.\n"
                    "🏥 *`/cemil-health`* - Bot sağlık durumunu kontrol et.\n\n"
                    "Güzel bir gün dilerim! ✨"
                )
                
                if settings.github_repo and "SİZİN_KULLANICI_ADINIZ" not in settings.github_repo:
                    startup_text += f"\n\n📚 *Kaynaklar:*\n"
                    startup_text += f"• <{settings.github_repo}/blob/main/README.md|Kullanım Kılavuzu>\n"
                    startup_text += f"• <{settings.github_repo}/blob/main/CHANGELOG.md|Neler Yeni?>\n"
                    startup_text += f"• <{settings.github_repo}/blob/main/CONTRIBUTING.md|Katkıda Bulun>"

                startup_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": startup_text + "\n<!channel>"
                        }
                    }
                ]

                chat_manager.post_message(
                    channel=settings.startup_channel,
                    text=startup_text,
                    blocks=startup_blocks
                )
                logger.info(f"[+] Başlangıç mesajı gönderildi: {settings.startup_channel}")
                print(f"[+] Başlangıç mesajı gönderildi: {settings.startup_channel}")
            except Exception as e:
                logger.error(f"[X] Başlangıç mesajı gönderilemedi: {e}", exc_info=True)
                print(f"[X] Başlangıç mesajı gönderilemedi: {e}")
        else:
            print("[i] Başlangıç mesajı atlandı.")
            logger.info("[i] Başlangıç mesajı kullanıcı tarafından atlandı.")
    else:
        print("[i] SLACK_STARTUP_CHANNEL tanımlı değil, başlangıç mesajı gönderilmeyecek.")
    
    print("\n" + "="*60)
    print("           BOT HAZIR - BAĞLANTI KURULUYOR")
    print("="*60 + "\n")
    
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()
