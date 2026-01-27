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

    # İzin verilen komut kanalları (opsiyonel)
    allowed_channels_raw = settings.allowed_command_channels or ""
    ALLOWED_COMMAND_CHANNELS = {
        c.strip() for c in allowed_channels_raw.split(",") if c.strip()
    }

    @app.command("/challenge")
    def handle_challenge_command(ack, body):
        """Challenge komutları."""
        ack()
        user_id = body["user_id"]
        channel_id = body["channel_id"]

        # Kanal kısıtı: Eğer liste boş değilse ve bu kanal listede yoksa çalışmasın
        if ALLOWED_COMMAND_CHANNELS and channel_id not in ALLOWED_COMMAND_CHANNELS:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="⚠️ Bu komutu sadece belirli kanallarda kullanabilirsiniz."
            )
            return
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
                    "`/challenge set github <link>` - Değerlendirme kanalında GitHub repo linki ekleyin\n"
                    "`/challenge force [success|fail]` - (Admin) Değerlendirmeyi zorla bitir\n\n"
                    "Örnek: `/challenge start 4`\n\n"
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
        elif subcommand == "force":
            handle_challenge_force(subcommand_text, user_id, channel_id)
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
        """Challenge başlatma - Tema seçim butonlarını göster."""
        try:
            request = ChallengeStartRequest.parse_from_text(text)
        except ValueError as ve:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Format hatası: {str(ve)}\n\nÖrnek: `/challenge start 4`"
            )
            return

        # Mevcut temaları veritabanından çek
        from src.repositories import ChallengeThemeRepository
        from src.clients import DatabaseClient
        
        db_client = DatabaseClient(db_path=settings.database_path)
        theme_repo = ChallengeThemeRepository(db_client)
        active_themes = theme_repo.get_active_themes()
        
        if not active_themes:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Aktif tema bulunamadı. Lütfen yöneticiyle iletişime geçin."
            )
            return

        # Tema seçim butonlarını oluştur (ikonlar veritabanından)
        theme_buttons = []
        
        for theme in active_themes:
            # Veritabanından icon alanını al, yoksa default kullan
            icon = theme.get("icon", "🎯")
            theme_buttons.append({
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": f"{icon} {theme['name']}",
                    "emoji": True
                },
                "action_id": f"challenge_theme_select_{theme['id']}",
                "value": f"{request.team_size}|{theme['id']}|{theme['name']}|{channel_id}"
            })
        
        # Random seçeneği ekle
        theme_buttons.append({
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "🎲 Random",
                "emoji": True
            },
            "style": "primary",
            "action_id": "challenge_theme_select_random",
            "value": f"{request.team_size}|random|Random|{channel_id}"
        })

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"🎯 *{request.team_size + 1} Kişilik Challenge Başlatılıyor*\n\n"
                        "Bir tema seçin:"
                    )
                }
            },
            {
                "type": "actions",
                "elements": theme_buttons
            }
        ]

        chat_manager.post_ephemeral(
            channel=channel_id,
            user=user_id,
            text="🎯 Challenge için tema seçin",
            blocks=blocks
        )

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
            
            # Challenge'ı bitirme isteği oluştur (Admin onayı için)
            try:
                result = await challenge_service.request_finish_challenge(
                    challenge_id=challenge["id"],
                    requester_id=user_id,
                    channel_id=channel_id
                )
                
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"]
                )
            except Exception as e:
                logger.error(f"[X] Challenge bitirme isteği hatası: {e}", exc_info=True)
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"❌ İşlem başarısız: {str(e)}"
                )
        
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

    def handle_challenge_force(text: str, user_id: str, channel_id: str):
        """Admin force komutu - Değerlendirmeyi zorla bitir."""
        if not text:
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Kullanım: `/challenge force [success|fail]`"
            )
            return

        async def process_force():
            # Admin yetki kontrolü
            settings = get_settings()
            ADMIN_USER_ID = settings.admin_slack_id
            
            if user_id != ADMIN_USER_ID:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Bu komutu sadece admin kullanabilir."
                )
                return

            # Bu kanal bir değerlendirme kanalı mı?
            from src.repositories import ChallengeEvaluationRepository
            from src.clients import DatabaseClient
            
            db_client = DatabaseClient(db_path=settings.database_path)
            eval_repo = ChallengeEvaluationRepository(db_client)
            
            evaluation_list = eval_repo.list(filters={"evaluation_channel_id": channel_id})
            if not evaluation_list:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Bu komut sadece değerlendirme kanalında kullanılabilir."
                )
                return
            
            evaluation = evaluation_list[0]
            
            # Komutu işle
            decision = text.lower().strip()
            
            if decision == "success":
                # Başarılı olarak işaretle (Yönetici Kararı)
                result = await evaluation_service.force_complete_evaluation(evaluation["id"], user_id, "success")
                
            elif decision == "fail":
                # Başarısız olarak işaretle (Yönetici Kararı)
                result = await evaluation_service.force_complete_evaluation(evaluation["id"], user_id, "failed")
                
            else:
                 chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Geçersiz seçenek. Kullanım: `/challenge force [success|fail]`"
                )
                 return

            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=result["message"]
            )

        asyncio.run(process_force())

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

    # Tema seçim butonu handler'ı
    @app.action(re.compile(r"^challenge_theme_select_.*"))
    def handle_theme_selection(ack, body):
        """Tema seçim butonuna tıklandığında challenge'ı başlat."""
        ack()
        
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        
        # Action'dan değerleri al
        actions = body.get("actions", [])
        if not actions:
            logger.warning(f"[!] Theme selection payload'ında action bulunamadı: {body}")
            return
        
        action = actions[0]
        action_value = action.get("value", "")
        
        # Value formatı: "team_size|theme_id|theme_name|original_channel_id"
        try:
            parts = action_value.split("|")
            team_size = int(parts[0])
            theme_id = parts[1]  # "random" veya gerçek tema ID
            theme_name = parts[2]
            original_channel_id = parts[3] if len(parts) > 3 else channel_id
        except (ValueError, IndexError) as e:
            logger.error(f"[X] Theme selection value parse hatası: {e}")
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Tema seçimi işlenirken bir hata oluştu."
            )
            return
        
        # Kullanıcı bilgisini al
        try:
            user_data = user_repo.get_by_slack_id(user_id)
            user_name = user_data.get('full_name', user_id) if user_data else user_id
        except Exception:
            user_name = user_id
        
        logger.info(f"[>] Tema seçildi: {theme_name} | Kullanıcı: {user_name} ({user_id}) | Takım: {team_size + 1}")
        
        async def process_start_with_theme():
            # theme_id "random" ise None gönder (random seçilecek)
            selected_theme = None if theme_id == "random" else theme_name
            
            result = await challenge_service.start_challenge(
                creator_id=user_id,
                team_size=team_size,
                channel_id=original_channel_id,
                theme=selected_theme  # Yeni parametre
            )

            if result["success"]:
                theme_info = f" | Tema: {theme_name}" if theme_name != "Random" else " | Tema: Random (takım dolunca seçilecek)"
                success_msg = result["message"] + f"\n\n🎨 *Seçilen Tema:* {theme_name}"
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=success_msg,
                    blocks=[{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": success_msg
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


        asyncio.run(process_start_with_theme())

    @app.action("admin_approve_finish_challenge")
    def handle_admin_approve_finish(ack, body):
        """Admin challenge bitirmeyi onayladığında."""
        ack()
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]
        
        # Admin kontrolü (Slack'te admin kanalı yetkisi yeterli ama garanti olsun)
        # Şimdilik sadece logluyoruz, action zaten admin kanalında
        
        action_value = body["actions"][0]["value"]
        # value: challenge_id|channel_id|requester_id
        parts = action_value.split("|")
        challenge_id = parts[0]
        challenge_channel_id = parts[1]
        requester_id = parts[2]
        
        logger.info(f"[admin] Challenge bitirme ONAYLANDI | Admin: {user_id} | Challenge: {challenge_id}")
        
        async def process_approval():
            # Challenge'ı kapat
            try:
                await challenge_service._close_challenge(challenge_id, challenge_channel_id)
                
                # Admin mesajını güncelle
                chat_manager.update_message(
                    channel=channel_id,
                    ts=message_ts,
                    text="✅ Challenge bitirme isteği ONAYLANDI",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"✅ *Challenge Bitirme İsteği ONAYLANDI*\n\nAdmin: <@{user_id}>\nChallenge: `{challenge_id[:8]}`"
                            }
                        }
                    ]
                )
                
                # İsteyen kullanıcıya bilgi ver (DM)
                try:
                    dm = chat_manager.client.conversations_open(users=requester_id)
                    dm_channel = dm["channel"]["id"]
                    chat_manager.post_message(
                        channel=dm_channel,
                        text=f"✅ Challenge (`{challenge_id[:8]}`) bitirme isteğiniz ONAYLANDI ve challenge sonlandırıldı."
                    )
                except Exception as e:
                    logger.warning(f"Kullanıcıya DM atılamadı: {e}")
                    
            except Exception as e:
                logger.error(f"Approval process error: {e}")
                chat_manager.post_message(
                    channel=channel_id,
                    text=f"❌ İşlem sırasında hata oluştu: {str(e)}"
                )
        
        asyncio.run(process_approval())

    @app.action("admin_reject_finish_challenge")
    def handle_admin_reject_finish(ack, body):
        """Admin challenge bitirmeyi reddettiğinde."""
        ack()
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]
        
        action_value = body["actions"][0]["value"]
        parts = action_value.split("|")
        challenge_id = parts[0]
        requester_id = parts[2]
        
        logger.info(f"[admin] Challenge bitirme REDDEDİLDİ | Admin: {user_id} | Challenge: {challenge_id}")
        
        # Admin mesajını güncelle
        try:
            chat_manager.update_message(
                channel=channel_id,
                ts=message_ts,
                text="❌ Challenge bitirme isteği REDDEDİLDİ",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"❌ *Challenge Bitirme İsteği REDDEDİLDİ*\n\nAdmin: <@{user_id}>\nChallenge: `{challenge_id[:8]}`"
                        }
                    }
                ]
            )
            
            # İsteyen kullanıcıya bilgi ver (DM)
            try:
                dm = chat_manager.client.conversations_open(users=requester_id)
                dm_channel = dm["channel"]["id"]
                chat_manager.post_message(
                    channel=dm_channel,
                    text=f"❌ Challenge (`{challenge_id[:8]}`) bitirme isteğiniz REDDEDİLDİ."
                )
            except Exception as e:
                logger.warning(f"Kullanıcıya DM atılamadı: {e}")
                
        except Exception as e:
            logger.error(f"Rejection process error: {e}")

    @app.action("admin_finish_details")
    def handle_admin_finish_details(ack, body):
        """Admin detay butonuna tıkladığında."""
        ack()
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        
        action_value = body["actions"][0]["value"]
        parts = action_value.split("|")
        challenge_id = parts[0]
        challenge_channel_id = parts[1]
        requester_id = parts[2]
        
        # Challenge detaylarını veritabanından çek
        # (Servis yerine buradan DB veya Repo çağırabiliriz ama service kullanmak daha temiz)
        # Ancak burada servise gitmeden hızlıca repo kullanacağız.
        
        # Handler içinde repository initiate etmek yerine, service üzerinden çağırmadım çünkü context dışında.
        # Basitçe ephemeral mesaj dönelim.
        
        async def show_details():
            try:
                # Challenge detaylarını almak için servisi kullan (zaten yukarıda import edilmiş)
                challenge = challenge_service.hub_repo.get(challenge_id)
                if not challenge:
                    chat_manager.post_ephemeral(
                        channel=channel_id,
                        user=user_id,
                        text="❌ Challenge bulunamadı."
                    )
                    return

                detail_text = (
                    f"🛑 *Challenge Detayları*\n\n"
                    f"🆔 *ID:* `{challenge_id}`\n"
                    f"👤 *İsteyen:* <@{requester_id}>\n"
                    f"🎨 *Tema:* {challenge.get('theme', 'N/A')}\n"
                    f"📊 *Durum:* {challenge.get('status', 'N/A')}\n"
                    f"⏱️ *Bitiş:* {challenge.get('deadline', 'N/A')}\n"
                    f"📢 *Kanal:* <#{challenge_channel_id}>\n\n"
                    f"💡 *Takım:* {challenge.get('team_size', 0)} kişi"
                )
                
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=detail_text
                )
            except Exception as e:
                logger.error(f"Details error: {e}")
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Detaylar alınırken hata oluştu."
                )
        
        asyncio.run(show_details())

    @app.action("challenge_join_jury_toggle")
    def handle_jury_toggle(ack, body):
        """Jüri katıl/çık (toggle) butonu."""
        ack()
        
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        
        actions = body.get("actions", [])
        if not actions: return
        
        evaluation_id = actions[0]["value"]
        
        logger.info(f"[admin] Jüri toggle tıklandı: {user_id} | Eval: {evaluation_id}")
        
        async def process_toggle():
            try:
                result = await evaluation_service.toggle_juror(evaluation_id, user_id)
                
                # Kullanıcıya bilgi ver (Ephemeral)
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text=result["message"]
                )
                
                # Buton üzerindeki sayıyı güncelle (Eğer mümkünse)
                # Not: Slack'te butonu herkese farklı gösteremeyiz, ancak blokları güncelleyerek
                # tüm kanal için sayıyı güncelleyebiliriz (X/3).
                if result.get("success") and result.get("action") in ["joined", "left"]:
                    new_count = result.get("count", 0)
                    is_full = result.get("is_full", False)
                    
                    # Mesajı güncelle
                    message_ts = body["message"]["ts"]
                    original_blocks = body["message"]["blocks"]
                    
                    import copy
                    new_blocks = copy.deepcopy(original_blocks)
                    
                    # Butonu bul ve güncelle veya kaldır
                    for block in new_blocks:
                        if block.get("type") == "actions":
                            if is_full:
                                # Dolduysa butonu kaldır ve bilgi mesajı ekle
                                block["elements"] = [] # Elementleri boşalt (veya bloğu sil)
                            else:
                                # Dolmadıysa sayıyı güncelle
                                for elem in block["elements"]:
                                    if elem.get("action_id") == "challenge_join_jury_toggle":
                                        elem["text"]["text"] = f"🙋 Jüri Ol ({new_count}/3)"
                    
                    # Eğer dolduysa actions bloğunu tamamen kaldırabiliriz veya "Jüri Tamamlandı" yazabiliriz
                    if is_full:
                        new_blocks = [b for b in new_blocks if b.get("type") != "actions"]
                        new_blocks.append({
                            "type": "context",
                            "elements": [{"type": "mrkdwn", "text": "✅ *Jüri Ekibi Tamamlandı! Değerlendirme başladı.*"}]
                        })

                    chat_manager.update_message(
                        channel=channel_id,
                        ts=message_ts,
                        text="🗳️ Jüri Aranıyor (Güncellendi)",
                        blocks=new_blocks
                    )

            except Exception as e:
                logger.error(f"Jury toggle error: {e}")
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ İşlem sırasında hata oluştu."
                )

        asyncio.run(process_toggle())