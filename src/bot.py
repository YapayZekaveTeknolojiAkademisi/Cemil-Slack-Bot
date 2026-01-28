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
    UserManager,
    CanvasManager
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
    setup_daily_handlers,
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
canvas_manager = CanvasManager(app.client)
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
    challenge_hub_repo, challenge_participant_repo,
    user_challenge_stats_repo, cron_client,
    canvas_manager, user_manager
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
setup_daily_handlers(app, groq_client, chat_manager)
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

# Takımı dolmayan challenge'ları periyodik olarak kontrol et (her gün 03:00'da)
try:
    cron_client.add_cron_job(
        func=challenge_hub_service.monitor_recruitment_timeouts,
        cron_expression={"hour": "3", "minute": "0"},
        job_id="monitor_recruitment_timeouts"
    )
    logger.info("[+] Challenge recruitment zaman aşımı kontrolü başlatıldı (her gün 03:00)")
except Exception as e:
    logger.warning(f"[!] Challenge recruitment zaman aşımı kontrolü başlatılamadı: {e}")

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
# BOT BAŞLATMA
# ============================================================================

# src/bot.py dosyasındaki ana bot nesnesi ve handlerlar hazır.
# Başlatma işlemi src/__main__.py üzerinden gerçekleştirilir.
