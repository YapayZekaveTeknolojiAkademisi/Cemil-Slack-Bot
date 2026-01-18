"""
Challenge Hub komut handler'ları.
"""

import asyncio
from slack_bolt import App
from src.core.logger import logger
from src.core.settings import get_settings
from src.core.rate_limiter import get_rate_limiter
from src.core.validators import ChallengeStartRequest, ChallengeJoinRequest
from src.commands import ChatManager
from src.services import ChallengeHubService
from src.repositories import UserRepository


def setup_challenge_handlers(
    app: App,
    challenge_service: ChallengeHubService,
    chat_manager: ChatManager,
    user_repo: UserRepository
):
    """Challenge handler'larını kaydeder."""
    settings = get_settings()
    rate_limiter = get_rate_limiter(
        max_requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window
    )

    @app.command("/challenge")
    def handle_challenge_command(ack, body):
        """Challenge komutları."""
        ack()
        user_id = body["user_id"]
        channel_id = body["channel_id"]
        text = body.get("text", "").strip()

        # Komut parse et
        parts = text.split(maxsplit=1)
        if not parts:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "📋 *Challenge Komutları:*\n\n"
                    "`/challenge start <takım> \"<tema>\" [süre] [zorluk]` - Yeni challenge başlat\n"
                    "`/challenge join [challenge_id]` - Challenge'a katıl\n"
                    "`/challenge status` - Challenge durumunu görüntüle\n\n"
                    "Örnek: `/challenge start 4 \"AI Chatbot\" 48 intermediate`"
                )
            )
            return

        subcommand = parts[0].lower()
        subcommand_text = parts[1] if len(parts) > 1 else ""

        # Kullanıcı bilgisini al
        try:
            user_data = user_repo.get_by_slack_id(user_id)
            user_name = user_data.get('full_name', user_id) if user_data else user_id
        except Exception:
            user_name = user_id

        logger.info(f"[>] /challenge {subcommand} komutu geldi | Kullanıcı: {user_name} ({user_id})")

        # Rate limiting
        allowed, error_msg = rate_limiter.is_allowed(user_id)
        if not allowed:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=error_msg
            )
            return

        if subcommand == "start":
            handle_start_challenge(subcommand_text, user_id, channel_id)
        elif subcommand == "join":
            handle_join_challenge(subcommand_text, user_id, channel_id)
        elif subcommand == "status":
            handle_challenge_status(user_id, channel_id)
        else:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Bilinmeyen komut: {subcommand}"
            )

    def handle_start_challenge(text: str, user_id: str, channel_id: str):
        """Challenge başlatma."""
        try:
            request = ChallengeStartRequest.parse_from_text(text)
        except ValueError as ve:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Format hatası: {str(ve)}\n\nÖrnek: `/challenge start 4 \"AI Chatbot\" 48 intermediate`"
            )
            return

        async def process_start():
            result = await challenge_service.start_challenge(
                creator_id=user_id,
                theme=request.theme,
                team_size=request.team_size,
                deadline_hours=request.deadline_hours,
                difficulty=request.difficulty
            )

            if result["success"]:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"]
                )
            else:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"]
                )

        asyncio.run(process_start())

    def handle_join_challenge(text: str, user_id: str, channel_id: str):
        """Challenge'a katılma."""
        try:
            request = ChallengeJoinRequest.parse_from_text(text)
        except ValueError as ve:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Format hatası: {str(ve)}"
            )
            return

        async def process_join():
            result = await challenge_service.join_challenge(
                challenge_id=request.challenge_id,
                user_id=user_id
            )

            if result["success"]:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"]
                )
            else:
                error_msg = result["message"]
                if result.get("error_code") == "ALREADY_PARTICIPATING":
                    error_msg = (
                        "❌ *Zaten Bu Challenge'a Katıldınız*\n\n"
                        "Aynı challenge'a iki kez katılamazsınız. "
                        "Başka bir challenge'a katılabilir veya yeni bir challenge başlatabilirsiniz."
                    )
                
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=error_msg
                )

        asyncio.run(process_join())

    def handle_challenge_status(user_id: str, channel_id: str):
        """Challenge durumunu göster."""
        # TODO: Implement
        chat_manager.post_ephemeral(
            channel=channel_id,
            user=user_id,
            text="📊 Challenge durumu özelliği yakında eklenecek."
        )
