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
from slack_sdk import WebClient
from datetime import datetime

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
    UserChallengeStatsRepository,
    ChallengeEvaluationRepository,
    ChallengeEvaluatorRepository
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
    ChallengeHubService,
    ChallengeEvaluationService
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
    setup_challenge_handlers,
    setup_challenge_evaluation_handlers
)

# Non-interactive mod (CI / prod deploy) için flag
NON_INTERACTIVE = os.environ.get("CEMIL_NON_INTERACTIVE") == "1"

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

# User token varsa kanal oluşturma ve erişim için kullan
user_client = None
if settings.slack_user_token:
    user_client = WebClient(token=settings.slack_user_token)
    logger.info("[i] User token bulundu - kanal oluşturma ve erişim işlemleri için kullanılacak")
else:
    logger.warning("[!] User token bulunamadı - workspace kısıtlamaları kanal oluşturmayı engelleyebilir")

chat_manager = ChatManager(app.client, user_client=user_client)
conv_manager = ConversationManager(app.client, user_client=user_client)
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
challenge_evaluation_repo = ChallengeEvaluationRepository(db_client)
challenge_evaluator_repo = ChallengeEvaluatorRepository(db_client)
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
challenge_evaluation_service = ChallengeEvaluationService(
    chat_manager, conv_manager,
    challenge_evaluation_repo, challenge_evaluator_repo,
    challenge_hub_repo, challenge_participant_repo, cron_client
)
challenge_hub_service = ChallengeHubService(
    chat_manager, conv_manager, user_manager,
    challenge_hub_repo, challenge_participant_repo,
    challenge_project_repo, challenge_submission_repo,
    challenge_theme_repo, user_challenge_stats_repo,
    challenge_enhancement_service, groq_client, cron_client,
    db_client=db_client,
    evaluation_service=challenge_evaluation_service
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
setup_challenge_handlers(app, challenge_hub_service, challenge_evaluation_service, chat_manager, user_repo)
setup_challenge_evaluation_handlers(app, challenge_evaluation_service, challenge_hub_service, chat_manager, user_repo)
logger.info("[+] Handler'lar kaydedildi.")

# ============================================================================
# PERİYODİK GÖREVLER (Challenge Kanalı Yetkisiz Kullanıcı Kontrolü)
# ============================================================================

# Challenge kanallarını periyodik olarak kontrol et (her 1 dakikada bir)
try:
    cron_client.add_cron_job(
        func=challenge_hub_service.monitor_challenge_channels,
        cron_expression={"minute": "*/1"},  # Her 1 dakikada bir
        job_id="monitor_challenge_channels"
    )
    logger.info("[+] Challenge kanalları periyodik kontrolü başlatıldı (her 1 dakikada bir)")
except Exception as e:
    logger.warning(f"[!] Challenge kanalları periyodik kontrolü başlatılamadı: {e}")

# Değerlendirmeleri periyodik olarak kontrol et (her 1 saatte bir)
def check_pending_evaluations():
    """Deadline'ı geçmiş değerlendirmeleri finalize et."""
    import asyncio
    try:
        pending = challenge_evaluation_repo.get_pending_evaluations()
        for evaluation in pending:
            asyncio.run(challenge_evaluation_service.finalize_evaluation(evaluation["id"]))
    except Exception as e:
        logger.error(f"[X] Pending evaluations kontrolü hatası: {e}", exc_info=True)

try:
    cron_client.add_cron_job(
        func=check_pending_evaluations,
        cron_expression={"minute": "0"},  # Her saat başı
        job_id="check_pending_evaluations"
    )
    logger.info("[+] Değerlendirme kontrolü başlatıldı (her 1 saatte bir)")
except Exception as e:
    logger.warning(f"[!] Değerlendirme kontrolü başlatılamadı: {e}")

# ============================================================================
# EVENT HANDLERS (Challenge Kanalı Yetkisiz Kullanıcı Kontrolü)
# ============================================================================

@app.event("member_joined_channel")
def handle_member_joined_channel(event, client):
    """
    Bir kullanıcı kanala katıldığında çağrılır.
    Challenge kanalları için yetkisiz kullanıcıları tespit edip çıkarır.
    """
    try:
        channel_id = event.get("channel")
        user_id = event.get("user")
        
        logger.info(f"[>] member_joined_channel event tetiklendi | Kullanıcı: {user_id} | Kanal: {channel_id}")
        
        if not channel_id or not user_id:
            logger.warning(f"[!] member_joined_channel event'inde eksik bilgi | channel_id: {channel_id} | user_id: {user_id}")
            return

        # Challenge kanalı kontrolü ve yetkisiz kullanıcı çıkarma
        result = challenge_hub_service.check_and_remove_unauthorized_user(channel_id, user_id)
        
        if result.get("is_challenge_channel") and not result.get("is_authorized"):
            action = result.get('action')
            logger.info(f"[!] Yetkisiz kullanıcı tespit edildi: {user_id} | Kanal: {channel_id} | Aksiyon: {action}")
            
            if action == "removed":
                logger.info(f"[+] Yetkisiz kullanıcı başarıyla çıkarıldı: {user_id}")
            elif action == "failed_to_remove":
                logger.error(f"[X] Yetkisiz kullanıcı çıkarılamadı: {user_id} | Kanal: {channel_id}")
            elif action == "error":
                logger.error(f"[X] Yetkisiz kullanıcı çıkarma işleminde hata: {result.get('error')}")
        elif result.get("is_challenge_channel") and result.get("is_authorized"):
            logger.debug(f"[i] Yetkili kullanıcı kanala katıldı: {user_id} | Kanal: {channel_id}")
        else:
            logger.debug(f"[i] Challenge kanalı değil, işlem yapılmadı: {channel_id}")
        
    except Exception as e:
        logger.error(f"[X] member_joined_channel event handler hatası: {e}", exc_info=True)

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
# ENGLISH CONVERSATION CLUB (DAILY)
# ============================================================================

# Gemini promptu: 2 kelime ve 3 topic oluşturuyor.
DAILY_SYSTEM_PROMPT = """You are the Coordinator for an English Conversation Club. 
I will provide you with TODAY'S DATE. Your task is to generate a 'Daily Discussion Card'.

STRICT OUTPUT RULES:
1. HEADER: 
   - Convert the provided date into full English text (e.g., "Twenty-First of January, Twenty-Twenty-Six").
2. TOPIC: 
   - Select a RANDOM, engaging, and unique topic suitable for A1-B2 levels. 
   - Do not repeat generic topics; be creative (e.g., Space Travel, Minimalist Living, Coffee Culture, Digital Nomads).
3. VOCABULARY: 
   - Create a Markdown table with EXACTLY 2 high-quality words related to the selected topic.
   - Format: | Word | Meaning (English) | Turkish (Brief) |
4. DISCUSSION QUESTIONS: 
   - Provide exactly 3 open-ended questions to start the conversation.
   - Start with bold prefixes (e.g., **The Idea:**).

STYLE:
- Minimalist, clean, and ready for Slack.
- No filler text like "Sure, here is the content". Just the content."""

@app.command("/daily")
def handle_daily_command(ack, say, command):
    #* 1. Slack'e komutu aldığımızı bildiriyoruz (Zorunlu)
    ack()
    
    user_id = command['user_id']
    user_text = command.get('text', '').strip()  # Kullanıcının yazdığı metin
    
    #* 2. VALIDATION (KAPI BEKÇİSİ)
    # Eğer kullanıcı tam olarak "English" yazmadıysa (büyük/küçük harf duyarsız) çalışmasın.
    if user_text.lower() != "english":
        #todo Kullanıcıya sadece kendisinin göreceği (ephemeral) bir hata mesajı dönülebilir
        # Ama şimdilik sessiz kalmasını veya basit bir uyarı vermesini sağlıyoruz.
        say(
            text="⚠️ Hatalı komut.",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ *Bu komut şu an sadece English Conversation Club için aktif.*\n\nLütfen çalıştırmak için tam olarak şunu yaz:\n👉 `/daily English`"
                    }
                }
            ]
        )
        return  # Fonksiyondan çık, yapay zekayı çalıştırma.

    #* 3. TARİH VE HAZIRLIK
    # Buraya geldiyse kullanıcı "English" yazmıştır.
    current_date = datetime.now().strftime("%d.%m.%Y")
    
    # Kullanıcıya bilgi veriyoruz
    say(f"🇬🇧 *English Conversation Club* ({current_date}) için içerik hazırlanıyor, beklerken belki bir /kahve ? :)")
    
    try:
        # 4. YAPAY ZEKA ÇAĞRISI (Asenkron)
        # GroqClient bir Singleton olduğu için direkt çağırıyoruz.
        client = GroqClient()
        
        # Async fonksiyonu, sync bir fonksiyon içinde çağırmak için asyncio.run kullanıyoruz.
        response = asyncio.run(
            client.quick_ask(
                system_prompt=DAILY_SYSTEM_PROMPT,
                user_prompt=f"Today is {current_date}. Generate the daily conversation card."
            )
        )
        
        # 5. SONUCU PAYLAŞ
        say(
            text="Daily Conversation Card", # Bildirimlerde görünen önizleme metni
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": response
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Generated for Conversation Club via Groq AI 🧠 | Requested by <@{user_id}>"
                        }
                    ]
                }
            ]
        )
        
    except Exception as e:
        # Hata loglaması (Projenin kendi logger'ını kullanarak)
        logger.error(f"Daily command error: {e}", exc_info=True)
        say(f"❌ Bir hata oluştu: {str(e)}")

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
            
            if NON_INTERACTIVE:
                choice = "h"
            else:
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
        
        if NON_INTERACTIVE:
            choice = "h"
        else:
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
        if NON_INTERACTIVE:
            choice = "h"
        else:
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
        if NON_INTERACTIVE:
            choice = "h"
        else:
            choice = input("Başlangıç mesajı (welcome) gönderilsin mi? (e/h): ").lower().strip()
        
        if choice == 'e':
            try:
                startup_text = (
                    "👋 *Merhabalar! Ben Cemil, göreve hazırım!* ☀️\n\n"
                    "Topluluk etkileşimini artırmak için buradayım. İşte güncel yeteneklerim ve özet akışlar:\n\n"
                    "☕ *`/kahve`* - Kahve molası eşleşmesi için havuza katıl; başka biri de isterse özel bir sohbet kanalı açılır ve 5 dakika sonra kapanır.\n"
                    "🆘 *`/yardim-iste`* - Topluluktan yardım iste; yardım kanalı açılır, 10 dakika sonra otomatik kapanır ve özet DM'ine gelir.\n"
                    "🚀 *`/challenge`* - Mini hackathon sistemi:\n"
                    "   • `/challenge start N` ile ilan aç, takım dolunca özel kanal + proje otomatik gelir.\n"
                    "   • Süre dolunca kanal kapanır ve \"Projeyi Değerlendir\" butonu ile 48 saatlik değerlendirme başlar.\n"
                    "   • Değerlendirme kanalında `/challenge set True/False` ve `/challenge set github <link>` komutlarıyla proje oylanır.\n"
                    "🧠 *`/sor`* - Dökümanlara ve bilgi küpüne soru sor.\n"
                    "🗳️ *`/oylama`* - Hızlı anketler başlat (Admin).\n"
                    "📝 *`/geri-bildirim`* - Yönetime anonim mesaj gönder.\n"
                    "👤 *`/profilim`* - Kayıtlı bilgilerini görüntüle.\n"
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
