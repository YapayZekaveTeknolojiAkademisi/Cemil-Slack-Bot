"""
Challenge Hub komut handler'ları.
"""

import asyncio
import re
from slack_bolt import App
from src.core.logger import logger
from src.core.settings import get_settings
from src.core.rate_limiter import get_rate_limiter
from src.core.validators import ChallengeStartRequest, ChallengeJoinRequest
from src.commands import ChatManager
from src.services import ChallengeHubService, ChallengeEvaluationService
from src.repositories import UserRepository


def setup_challenge_handlers(
    app: App,
    challenge_service: ChallengeHubService,
    evaluation_service: ChallengeEvaluationService,
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
                    "`/challenge start <takım>` - Yeni challenge başlat (tema ve proje random seçilir)\n"
                    "`/challenge join [challenge_id]` - Challenge'a katıl\n"
                    "`/challenge status` - Challenge durumunu görüntüle\n"
                    "`/challenge bitir` - Challenge'ı bitir ve değerlendirmeyi başlat (challenge kanalında)\n"
                    "`/challenge set True/False` - Değerlendirme kanalında oy verin\n"
                    "`/challenge set github <link>` - Değerlendirme kanalında GitHub repo linki ekleyin\n\n"
                    "Örnek: `/challenge start 4`\n\n"
                    "💡 *Not:* Tema ve proje takım dolunca otomatik olarak random seçilir."
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
                text=error_msg,
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": error_msg
                    }
                }]
            )
            return

        if subcommand == "start":
            handle_start_challenge(subcommand_text, user_id, channel_id)
        elif subcommand == "join":
            handle_join_challenge(subcommand_text, user_id, channel_id)
        elif subcommand == "status":
            handle_challenge_status(user_id, channel_id)
        elif subcommand == "bitir":
            handle_challenge_finish(user_id, channel_id)
        elif subcommand == "set":
            handle_challenge_set(subcommand_text, user_id, channel_id)
        else:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Bilinmeyen komut: {subcommand}",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"❌ Bilinmeyen komut: {subcommand}"
                    }
                }]
            )

    def handle_start_challenge(text: str, user_id: str, channel_id: str):
        """Challenge başlatma - Sadece kişi sayısı."""
        try:
            request = ChallengeStartRequest.parse_from_text(text)
        except ValueError as ve:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Format hatası: {str(ve)}\n\nÖrnek: `/challenge start 4`"
            )
            return

        async def process_start():
            result = await challenge_service.start_challenge(
                creator_id=user_id,
                team_size=request.team_size,
                channel_id=channel_id  # Mesajı komutun çalıştırıldığı kanala gönder
            )

            if result["success"]:
                # \n karakterlerinin çalışması için blocks kullan
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"],
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": result["message"]
                        }
                    }]
                )
            else:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"],
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": result["message"]
                        }
                    }]
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
                text=f"❌ Format hatası: {str(ve)}",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"❌ Format hatası: {str(ve)}"
                    }
                }]
            )
            return

        async def process_join():
            result = await challenge_service.join_challenge(
                challenge_id=request.challenge_id,
                user_id=user_id
            )

            if result["success"]:
                # \n karakterlerinin çalışması için blocks kullan
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"],
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": result["message"]
                        }
                    }]
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
                    text=error_msg,
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": error_msg
                        }
                    }]
                )

        asyncio.run(process_join())

    def handle_challenge_status(user_id: str, channel_id: str):
        """Challenge durumunu göster."""
        async def process_status():
            # Kullanıcının aktif challenge'ını bul (katılımcı olarak VEYA creator olarak)
            from src.repositories import ChallengeParticipantRepository, ChallengeHubRepository
            from src.clients import DatabaseClient
            from src.core.settings import get_settings
            
            settings = get_settings()
            db_client = DatabaseClient(db_path=settings.database_path)
            participant_repo = ChallengeParticipantRepository(db_client)
            hub_repo = ChallengeHubRepository(db_client)
            
            # Önce katılımcı olarak bak
            active_challenges = participant_repo.get_user_active_challenges(user_id)
            
            # Katılımcı olarak bulamadıysa, creator olarak bak
            if not active_challenges:
                # Creator olarak aktif challenge'ları bul
                try:
                    with db_client.get_connection() as conn:
                        cursor = conn.cursor()
                        sql = """
                            SELECT * FROM challenge_hubs
                            WHERE creator_id = ? AND status IN ('recruiting', 'active')
                            ORDER BY created_at DESC
                        """
                        cursor.execute(sql, (user_id,))
                        rows = cursor.fetchall()
                        active_challenges = [dict(row) for row in rows]
                except Exception as e:
                    logger.error(f"[X] Creator challenge'ları alınırken hata: {e}")
            
            if not active_challenges:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="ℹ️ Aktif challenge'ınız yok. `/challenge start` ile yeni challenge başlatabilirsiniz.",
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "ℹ️ Aktif challenge'ınız yok. `/challenge start` ile yeni challenge başlatabilirsiniz."
                        }
                    }]
                )
                return
            
            # İlk aktif challenge'ı göster
            challenge = active_challenges[0]
            participants = participant_repo.get_team_members(challenge["id"])
            participant_count = len(participants)
            
            status_text = (
                f"📊 *Challenge Durumu*\n\n"
                f"*Tema:* {challenge.get('theme', 'N/A')}\n"
                f"*Takım:* {participant_count}/{challenge.get('team_size', 'N/A')} kişi\n"
                f"*Durum:* {challenge.get('status', 'N/A').upper()}\n"
                f"*Süre:* {challenge.get('deadline_hours', 'N/A')} saat\n"
            )
            
            if challenge.get("challenge_channel_id"):
                status_text += f"*Kanal:* <#{challenge['challenge_channel_id']}>\n"
            
            if challenge.get("status") == "recruiting":
                status_text += f"\n⏳ Takım dolması bekleniyor..."
            elif challenge.get("status") == "active":
                status_text += f"\n🚀 Challenge devam ediyor!"
            
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=status_text,
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": status_text
                    }
                }]
            )
        
        asyncio.run(process_status())

    def handle_challenge_finish(user_id: str, channel_id: str):
        """Challenge bitirme komutu - Challenge kanalında çalıştırılmalı."""
        async def process_finish():
            # Bu kanal bir challenge kanalı mı?
            from src.repositories import ChallengeHubRepository, ChallengeEvaluationRepository
            from src.clients import DatabaseClient
            from src.core.settings import get_settings
            
            settings = get_settings()
            db_client = DatabaseClient(db_path=settings.database_path)
            hub_repo = ChallengeHubRepository(db_client)
            
            challenge = hub_repo.get_by_channel_id(channel_id)
            if not challenge:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Bu komut sadece challenge kanalında kullanılabilir."
                )
                return
            
            # Challenge aktif mi?
            if challenge.get("status") != "active":
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"❌ Bu challenge zaten {challenge.get('status', 'bilinmeyen')} durumunda."
                )
                return
            
            # Zaten değerlendirme başlatılmış mı?
            eval_repo = ChallengeEvaluationRepository(db_client)
            existing = eval_repo.get_by_challenge(challenge["id"])
            if existing:
                # Mesajı hub kanalına veya mevcut kanala gönder (challenge kanalı arşivlenmiş olabilir)
                target_channel = challenge.get("hub_channel_id") or channel_id
                try:
                    chat_manager.post_ephemeral(
                        channel=target_channel,
                        user=user_id,
                        text="ℹ️ Bu challenge için değerlendirme zaten başlatılmış."
                    )
                except Exception as e:
                    logger.warning(f"[!] Challenge finish ephemeral (already started) gönderilemedi: {e}")
                return
            
            # Challenge'ı kapat (_close_challenge fonksiyonu değerlendirmeyi başlatır ve kanalı arşivler)
            try:
                await challenge_service._close_challenge(challenge["id"], channel_id)
                target_channel = challenge.get("hub_channel_id") or channel_id
                try:
                    chat_manager.post_ephemeral(
                        channel=target_channel,
                        user=user_id,
                        text="✅ Challenge bitirildi! Değerlendirme başlatıldı ve challenge kanalı arşivlendi."
                    )
                except Exception as e:
                    # Kanal arşivlenmişse veya başka bir Slack hatası varsa, sadece logla
                    logger.warning(f"[!] Challenge finish ephemeral gönderilemedi: {e}")
            except Exception as e:
                logger.error(f"[X] Challenge bitirme hatası: {e}", exc_info=True)
                target_channel = challenge.get("hub_channel_id") or channel_id
                try:
                    chat_manager.post_ephemeral(
                        channel=target_channel,
                        user=user_id,
                        text=f"❌ Challenge bitirilirken hata oluştu: {str(e)}"
                    )
                except Exception as inner_e:
                    logger.warning(f"[!] Challenge finish hata mesajı gönderilemedi: {inner_e}")
        
        asyncio.run(process_finish())

    def handle_challenge_set(text: str, user_id: str, channel_id: str):
        """Challenge set komutu - True/False/Github link."""
        if not text:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "📋 *Challenge Set Komutları:*\n\n"
                    "`/challenge set True` - Proje başarılı\n"
                    "`/challenge set False` - Proje başarısız\n"
                    "`/challenge set github <link>` - GitHub repo linki\n\n"
                    "💡 Bu komutlar sadece değerlendirme kanalında kullanılabilir."
                )
            )
            return

        async def process_set():
            # Değerlendirme kanalında mı kontrol et
            from src.repositories import ChallengeEvaluationRepository, ChallengeHubRepository
            from src.clients import DatabaseClient
            from src.core.settings import get_settings
            
            settings = get_settings()
            db_client = DatabaseClient(db_path=settings.database_path)
            eval_repo = ChallengeEvaluationRepository(db_client)
            hub_repo = ChallengeHubRepository(db_client)
            
            # Bu kanal bir değerlendirme kanalı mı?
            evaluation_list = eval_repo.list(filters={"evaluation_channel_id": channel_id})
            if not evaluation_list:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Bu komut sadece değerlendirme kanalında kullanılabilir."
                )
                return
            
            evaluation = evaluation_list[0]
            evaluation_id = evaluation["id"]
            
            # Komutu parse et
            parts = text.split(maxsplit=1)
            command = parts[0].lower()
            
            if command == "true":
                result = await evaluation_service.submit_vote(evaluation_id, user_id, "true")
            elif command == "false":
                result = await evaluation_service.submit_vote(evaluation_id, user_id, "false")
            elif command == "github":
                if len(parts) < 2:
                    chat_manager.post_ephemeral(
                        channel=channel_id,
                        user=user_id,
                        text="❌ GitHub linki gerekli. Örnek: `/challenge set github https://github.com/user/repo`"
                    )
                    return
                github_url = parts[1].strip()
                result = await evaluation_service.submit_github_link(evaluation_id, github_url)
            else:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Geçersiz komut. `/challenge set True`, `/challenge set False` veya `/challenge set github <link>` kullanın."
                )
                return
            
            # Sonucu göster
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=result["message"]
            )
        
        asyncio.run(process_set())

    @app.action("challenge_join_button")
    def handle_challenge_join_button(ack, body):
        """Challenge'a katıl butonuna tıklama."""
        ack()
        
        # Payload'ı logla (debug için)
        import json
        logger.debug(f"[DEBUG] Challenge join button payload: {json.dumps(body, indent=2, ensure_ascii=False)}")
        
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        
        # Action'dan challenge_id'yi al
        actions = body.get("actions", [])
        if not actions:
            logger.warning(f"[!] Challenge join button payload'ında action bulunamadı: {body}")
            return
        
        action = actions[0]
        challenge_id = action.get("value")
        action_id = action.get("action_id", "")
        
        # Eğer action_id "challenge_join_button" değilse (Slack'in otomatik oluşturduğu action_id olabilir)
        # veya value "joined" ise, zaten katıldı demektir
        if challenge_id == "joined" or (action_id != "challenge_join_button" and challenge_id == "joined"):
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="✅ Zaten bu challenge'a katıldınız.",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "✅ Zaten bu challenge'a katıldınız."
                    }
                }]
            )
            return
        
        # Kullanıcı bilgisini al
        try:
            user_data = user_repo.get_by_slack_id(user_id)
            user_name = user_data.get('full_name', user_id) if user_data else user_id
        except Exception:
            user_name = user_id
        
        logger.info(f"[>] Challenge join butonu tıklandı | Kullanıcı: {user_name} ({user_id}) | Challenge: {challenge_id}")
        
        async def process_join():
            result = await challenge_service.join_challenge(
                challenge_id=challenge_id,
                user_id=user_id
            )
            
            if result["success"]:
                # Başarılı mesajı gönder - \n karakterlerinin çalışması için blocks kullan
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"],
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": result["message"]
                        }
                    }]
                )
                
                # Mesajı güncelle - butonu disable et ve katılımcı sayısını güncelle
                try:
                    import copy
                    message_ts = body.get("message", {}).get("ts")
                    if not message_ts:
                        logger.debug("[i] Mesaj timestamp bulunamadı, güncelleme atlanıyor")
                        return
                    
                    blocks = copy.deepcopy(body["message"].get("blocks", []))
                    if not blocks:
                        logger.debug("[i] Mesaj blocks bulunamadı, güncelleme atlanıyor")
                        return
                    
                    # Challenge bilgisini al (servis üzerinden)
                    from src.repositories import ChallengeHubRepository, ChallengeParticipantRepository
                    from src.clients import DatabaseClient
                    from src.core.settings import get_settings
                    
                    settings = get_settings()
                    db_client = DatabaseClient(db_path=settings.database_path)
                    hub_repo = ChallengeHubRepository(db_client)
                    participant_repo = ChallengeParticipantRepository(db_client)
                    
                    challenge = hub_repo.get(challenge_id)
                    if not challenge:
                        logger.warning(f"[!] Challenge bulunamadı: {challenge_id}")
                        return
                    
                    participants = participant_repo.get_team_members(challenge_id)
                    participant_count = len(participants)
                    team_size = challenge["team_size"]
                    challenge_started = result.get("challenge_started", False)
                    
                    # Butonu güncelle: Sadece takım dolduğunda veya challenge başladığında kaldır
                    # NOT: Butonu kullanıcıya özel yapamayız, mesaj tüm kullanıcılar için aynı!
                    # Eğer kullanıcı zaten katıldıysa, service "ALREADY_PARTICIPATING" hatası döner.
                    updated_blocks = []
                    for block in blocks:
                        if block.get("type") == "actions":
                            # Takım dolduysa veya challenge başladıysa butonu kaldır
                            if challenge_started or participant_count >= team_size:
                                # Actions block'unu tamamen kaldır
                                continue
                            else:
                                # Butonu olduğu gibi bırak (tüm kullanıcılar için aktif kalmalı)
                                updated_blocks.append(block)
                        else:
                            # Context'i güncelle
                            if block.get("type") == "context" and challenge:
                                remaining = team_size - participant_count
                                if challenge_started:
                                    block["elements"][0]["text"] = f"📊 *{participant_count}/{team_size}* | 🎊 *CHALLENGE BAŞLATILDI!*"
                                elif remaining > 0:
                                    block["elements"][0]["text"] = f"📊 *{participant_count}/{team_size}* | ⏳ *{remaining} kişi* daha gerekli"
                                else:
                                    block["elements"][0]["text"] = f"📊 *{participant_count}/{team_size}* | 🎊 *TAKIM DOLDU!* 🚀 Başlatılıyor..."
                            updated_blocks.append(block)
                    
                    # Mesajı güncelle
                    if updated_blocks:
                        chat_manager.update_message(
                            channel=channel_id,
                            ts=message_ts,
                            text="🚀 Yeni challenge açıldı!",
                            blocks=updated_blocks
                        )
                        logger.info(f"[+] Challenge mesajı güncellendi: {message_ts}")
                except Exception as e:
                    logger.warning(f"[!] Mesaj güncelleme hatası: {e}", exc_info=True)
                
            else:
                # Hata mesajını direkt service'den al (daha detaylı ve tutarlı)
                # Service'den gelen mesajlar zaten güzel formatlı
                error_msg = result["message"]
                
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=error_msg,
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": error_msg
                        }
                    }]
                )
        
        asyncio.run(process_join())
    
    # Genel handler - Slack'in otomatik oluşturduğu action_id'leri handle etmek için
    # (örneğin, mesaj güncellenirken action_id kaldırıldığında Slack otomatik action_id oluşturur)
    @app.action(re.compile(r"^vTXk0$|^challenge_join_button$"))
    def handle_challenge_join_button_fallback(ack, body):
        """Challenge join butonu için fallback handler (Slack'in otomatik oluşturduğu action_id'ler için)."""
        # Önce normal handler'ı çağır
        handle_challenge_join_button(ack, body)