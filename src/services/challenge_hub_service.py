"""
Challenge Hub yönetim servisi.
"""

import json
import uuid
import random
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from src.core.logger import logger
from src.core.exceptions import CemilBotError
from src.commands import ChatManager, ConversationManager, UserManager
from src.repositories import (
    ChallengeHubRepository,
    ChallengeParticipantRepository,
    ChallengeProjectRepository,
    ChallengeSubmissionRepository,
    ChallengeThemeRepository,
    UserChallengeStatsRepository
)
from src.clients import GroqClient, CronClient
from src.core.settings import get_settings
from src.services import ChallengeEnhancementService


class ChallengeHubService:
    """
    Challenge Hub yönetim servisi.
    """

    def __init__(
        self,
        chat_manager: ChatManager,
        conv_manager: ConversationManager,
        user_manager: UserManager,
        challenge_hub_repo: ChallengeHubRepository,
        participant_repo: ChallengeParticipantRepository,
        project_repo: ChallengeProjectRepository,
        submission_repo: ChallengeSubmissionRepository,
        theme_repo: ChallengeThemeRepository,
        stats_repo: UserChallengeStatsRepository,
        enhancement_service: ChallengeEnhancementService,
        groq_client: GroqClient,
        cron_client: CronClient,
        db_client=None,
        evaluation_service=None
    ):
        self.chat = chat_manager
        self.conv = conv_manager
        self.user = user_manager
        self.hub_repo = challenge_hub_repo
        self.participant_repo = participant_repo
        self.project_repo = project_repo
        self.submission_repo = submission_repo
        self.theme_repo = theme_repo
        self.stats_repo = stats_repo
        self.enhancement = enhancement_service
        self.groq = groq_client
        self.cron = cron_client
        self.db_client = db_client
        self.evaluation_service = evaluation_service

    async def start_challenge(
        self,
        creator_id: str,
        team_size: int,
        channel_id: Optional[str] = None,
        theme: Optional[str] = None  # Yeni: Kullanıcının seçtiği tema
    ) -> Dict[str, Any]:
        """
        Yeni challenge başlatır.
        theme: Seçilen tema adı. None ise takım dolunca random seçilir.
        """
        try:
            # 0. Kullanıcının users tablosunda olup olmadığını kontrol et (foreign key için gerekli)
            if not self.db_client:
                logger.error("[X] db_client bulunamadı, kullanıcı kontrolü yapılamıyor")
            else:
                try:
                    with self.db_client.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT id FROM users WHERE slack_id = ?", (creator_id,))
                        user_exists = cursor.fetchone()
                        
                        if not user_exists:
                            # Kullanıcı yoksa otomatik ekle (minimal bilgilerle)
                            logger.info(f"[i] Kullanıcı users tablosunda yok, otomatik ekleniyor: {creator_id}")
                            user_id = str(uuid.uuid4())
                            cursor.execute("""
                                INSERT INTO users (id, slack_id, full_name, created_at, updated_at)
                                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """, (user_id, creator_id, f"User {creator_id}"))
                            conn.commit()
                            logger.info(f"[+] Kullanıcı otomatik eklendi: {creator_id} (ID: {user_id})")
                except Exception as e:
                    logger.warning(f"[!] Kullanıcı kontrolü/ekleme hatası: {e}")
                    # Hata olsa bile devam et, belki kullanıcı zaten var
            
            # 1. Kullanıcının aktif challenge'ı var mı? (Katılımcı VEYA creator olarak)
            # Bir kişi sadece tek bir aktif challenge'da bulunabilir!
            active_challenges = []
            
            # A) Katılımcı olarak aktif challenge'ları kontrol et
            try:
                participant_challenges = self.participant_repo.get_user_active_challenges(creator_id)
                if participant_challenges:
                    active_challenges.extend(participant_challenges)
            except Exception as e:
                logger.warning(f"[!] Participant challenge kontrolü hatası: {e}")
            
            # B) Creator olarak aktif challenge'ları kontrol et
            try:
                if self.db_client:
                    with self.db_client.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT * FROM challenge_hubs
                            WHERE creator_id = ? AND status IN ('recruiting', 'active')
                        """, (creator_id,))
                        rows = cursor.fetchall()
                        creator_challenges = [dict(row) for row in rows]
                        if creator_challenges:
                            active_challenges.extend(creator_challenges)
            except Exception as e:
                logger.warning(f"[!] Creator challenge kontrolü hatası: {e}")
            
            # Eğer herhangi bir aktif challenge varsa (katılımcı veya creator), yeni challenge açamaz
            if active_challenges:
                challenge_info = active_challenges[0]
                challenge_status = challenge_info.get('status', 'unknown')
                challenge_id = challenge_info.get('id', 'unknown')[:8]
                
                return {
                    "success": False,
                    "message": (
                        f"❌ *Zaten Aktif Bir Challenge'ınız Var!*\n\n"
                        f"📊 *Durum:* {challenge_status.upper()}\n"
                        f"🆔 *Challenge ID:* `{challenge_id}...`\n\n"
                        f"💡 *Not:* Bir kişi aynı anda sadece tek bir aktif challenge'da bulunabilir.\n"
                        f"Mevcut challenge'ınızı tamamladıktan sonra yeni bir challenge başlatabilirsiniz."
                    ),
                    "error_code": "USER_HAS_ACTIVE_CHALLENGE"
                }

            # 2. Challenge hub oluştur (tema ve süre henüz belirlenmedi)
            challenge_id = str(uuid.uuid4())

            hub_data = {
                "id": challenge_id,
                "creator_id": creator_id,
                "theme": theme if theme else "TBD",  # Seçilen tema veya TBD
                "team_size": team_size,
                "status": "recruiting",
                "deadline_hours": 0,  # Proje seçilince DB'den gelecek
                "difficulty": "TBD"  # Proje seçilince belirlenecek
            }

            self.hub_repo.create(hub_data)

            # 2.5. Creator'ın total_challenges istatistiğini artır
            try:
                self.stats_repo.increment_total(creator_id)
                logger.debug(f"[i] Creator total_challenges güncellendi: {creator_id}")
            except Exception as e:
                logger.warning(f"[!] Creator istatistik güncelleme hatası: {e}")

            # 3. Challenge mesajını gönder (buton ile)
            # NOT: Creator'ı challenge_participants tablosuna ekleme,
            # zaten challenge_hubs.creator_id'de tutuluyor.
            # Böylece team_size sadece katılımcıları sayar (creator hariç).
            target_channel = channel_id or self._get_hub_channel()
            if target_channel:
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"👤 <@{creator_id}> *{team_size + 1} kişilik challenge başlattı*\n\n"
                                "👇 *Katılmak için butona tıklayın:*"
                            )
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "🎯 Challenge'a Katıl",
                                    "emoji": True
                                },
                                "style": "primary",
                                "action_id": "challenge_join_button",
                                "value": challenge_id
                            }
                        ]
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"📊 *0/{team_size}* katılımcı"
                            }
                        ]
                    }
                ]
                self.chat.post_message(
                    channel=target_channel,
                    text=f"👤 {creator_id} {team_size + 1} kişilik challenge başlattı",
                    blocks=blocks
                )
                
                # Hub channel ID'yi kaydet
                self.hub_repo.update(challenge_id, {"hub_channel_id": target_channel})

            logger.info(f"[+] Challenge başlatıldı | ID: {challenge_id} | Creator: {creator_id} | Takım Büyüklüğü (creator hariç): {team_size}")

            return {
                "success": True,
                "challenge_id": challenge_id,
                "message": (
                    f"✅ *{team_size + 1} kişilik challenge başlatıldı!*\n\n"
                    f"📊 *0/{team_size}* katılımcı\n\n"
                    "💡 Takım dolunca otomatik başlayacak."
                )
            }

        except Exception as e:
            logger.error(f"[X] ChallengeHubService.start_challenge hatası: {e}", exc_info=True)
            return {
                "success": False,
                "message": "❌ Challenge başlatılırken bir hata oluştu.",
                "error_code": "START_ERROR"
            }

    async def join_challenge(
        self,
        challenge_id: Optional[str],
        user_id: str
    ) -> Dict[str, Any]:
        """
        Challenge'a katılır.
        """
        try:
            # 0. Kullanıcının users tablosunda olup olmadığını kontrol et (foreign key için gerekli)
            if self.db_client:
                try:
                    with self.db_client.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT id FROM users WHERE slack_id = ?", (user_id,))
                        user_exists = cursor.fetchone()
                        
                        if not user_exists:
                            # Kullanıcı yoksa otomatik ekle (minimal bilgilerle)
                            logger.info(f"[i] Kullanıcı users tablosunda yok, otomatik ekleniyor: {user_id}")
                            user_uuid = str(uuid.uuid4())
                            cursor.execute("""
                                INSERT INTO users (id, slack_id, full_name, created_at, updated_at)
                                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """, (user_uuid, user_id, f"User {user_id}"))
                            conn.commit()
                            logger.info(f"[+] Kullanıcı otomatik eklendi: {user_id} (ID: {user_uuid})")
                except Exception as e:
                    logger.warning(f"[!] Kullanıcı kontrolü/ekleme hatası: {e}")
                    # Hata olsa bile devam et, belki kullanıcı zaten var
            
            # 1. Challenge bul
            if challenge_id:
                challenge = self.hub_repo.get(challenge_id)
            else:
                challenge = self.hub_repo.get_active_challenge()

            if not challenge:
                return {
                    "success": False,
                    "message": "❌ Aktif challenge bulunamadı.",
                    "error_code": "NO_ACTIVE_CHALLENGE"
                }

            challenge_id = challenge["id"]

            # 2. Kullanıcı challenge'ın creator'ı mı? (Creator otomatik olarak eklenecek, butona basmasına gerek yok)
            if user_id == challenge.get("creator_id"):
                return {
                    "success": False,
                    "message": "✅ Siz bu challenge'ın sahibisiniz! Takım dolunca otomatik olarak challenge kanalına ekleneceksiniz.",
                    "error_code": "USER_IS_CREATOR"
                }

            # 3. Kullanıcı zaten katılmış mı?
            existing = self.participant_repo.get_by_challenge_and_user(challenge_id, user_id)
            if existing:
                return {
                    "success": False,
                    "message": "❌ Zaten bu challenge'a katıldınız. Aynı challenge'a iki kez katılamazsınız.",
                    "error_code": "ALREADY_PARTICIPATING"
                }

            # 4. Challenge durumu kontrolü
            if challenge["status"] != "recruiting":
                return {
                    "success": False,
                    "message": "❌ Bu challenge'a katılım kabul edilmiyor (dolu veya başlamış).",
                    "error_code": "CHALLENGE_NOT_RECRUITING"
                }

            # 5. Takım dolu mu?
            current_participants = self.participant_repo.get_team_members(challenge_id)
            if len(current_participants) >= challenge["team_size"]:
                return {
                    "success": False,
                    "message": "❌ Bu challenge'ın takımı dolmuş.",
                    "error_code": "TEAM_FULL"
                }

            # 6. Kullanıcının başka aktif challenge'ı var mı? (Katılımcı VEYA creator olarak)
            # Bir kişi sadece tek bir aktif challenge'da bulunabilir!
            active_challenges = []
            
            # A) Katılımcı olarak aktif challenge'ları kontrol et (mevcut challenge hariç)
            try:
                participant_challenges = self.participant_repo.get_user_active_challenges(user_id)
                if participant_challenges:
                    # Mevcut challenge'a katılmaya çalışıyor, onu hariç tut
                    other_challenges = [c for c in participant_challenges if c.get("id") != challenge_id]
                    if other_challenges:
                        active_challenges.extend(other_challenges)
            except Exception as e:
                logger.warning(f"[!] Participant challenge kontrolü hatası: {e}")
            
            # B) Creator olarak aktif challenge'ları kontrol et
            try:
                if self.db_client:
                    with self.db_client.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT * FROM challenge_hubs
                            WHERE creator_id = ? AND status IN ('recruiting', 'active')
                        """, (user_id,))
                        rows = cursor.fetchall()
                        creator_challenges = [dict(row) for row in rows]
                        if creator_challenges:
                            active_challenges.extend(creator_challenges)
            except Exception as e:
                logger.warning(f"[!] Creator challenge kontrolü hatası: {e}")
            
            # Eğer herhangi bir aktif challenge varsa (katılımcı veya creator), yeni challenge'a katılamaz
            if active_challenges:
                challenge_info = active_challenges[0]
                challenge_status = challenge_info.get('status', 'unknown')
                other_challenge_id = challenge_info.get('id', 'unknown')[:8]
                
                return {
                    "success": False,
                    "message": (
                        f"❌ *Zaten Aktif Bir Challenge'ınız Var!*\n\n"
                        f"📊 *Durum:* {challenge_status.upper()}\n"
                        f"🆔 *Challenge ID:* `{other_challenge_id}...`\n\n"
                        f"💡 *Not:* Bir kişi aynı anda sadece tek bir aktif challenge'da bulunabilir.\n"
                        f"Mevcut challenge'ınızı tamamladıktan sonra başka bir challenge'a katılabilirsiniz."
                    ),
                    "error_code": "USER_HAS_ACTIVE_CHALLENGE"
                }

            # 7. Katılımcı ekle
            self.participant_repo.create({
                "id": str(uuid.uuid4()),
                "challenge_hub_id": challenge_id,
                "user_id": user_id,
                "role": "member"
            })

            # 7.5. Katılımcının total_challenges istatistiğini artır
            try:
                self.stats_repo.increment_total(user_id)
                logger.debug(f"[i] Katılımcı total_challenges güncellendi: {user_id}")
            except Exception as e:
                logger.warning(f"[!] Katılımcı istatistik güncelleme hatası: {e}")

            # 8. Takım doldu mu kontrol et
            updated_participants = self.participant_repo.get_team_members(challenge_id)
            participant_count = len(updated_participants)

            # 9. Takım dolduysa challenge'ı başlat
            challenge_started = False
            challenge_start_error = False
            if participant_count >= challenge["team_size"]:
                try:
                    await self._start_challenge(challenge_id)
                    challenge_started = True
                    logger.info(f"[+] Challenge otomatik başlatıldı | ID: {challenge_id} | Takım: {participant_count}/{challenge['team_size']}")
                except Exception as e:
                    logger.error(f"[X] Challenge başlatılırken hata: {e}", exc_info=True)
                    challenge_start_error = True
                    # Hata olsa bile kullanıcıya katılım başarısı mesajı gönder

            # 10. Hub kanalına güncelleme (eğer varsa) - Challenge başlatma işleminden SONRA
            hub_channel_id = challenge.get("hub_channel_id")
            if hub_channel_id:
                try:
                    remaining = challenge['team_size'] - participant_count
                    if challenge_started:
                        message_text = f"🎊 *TAKIM DOLDU!* 🚀 Challenge başlatıldı!"
                    elif challenge_start_error:
                        message_text = f"⚠️ *TAKIM DOLDU AMA BAŞLATMA HATASI!*"
                    elif remaining > 0:
                        message_text = f"🎉 Yeni katılımcı! 📊 *{participant_count}/{challenge['team_size']}* | ⏳ *{remaining} kişi* daha gerekli"
                    else:
                        message_text = f"🎊 *TAKIM DOLDU!* 🚀 Challenge başlatılıyor..."
                    
                    self.chat.post_message(
                        channel=hub_channel_id,
                        text=message_text,
                        blocks=[{
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": message_text
                            }
                        }]
                    )
                except Exception as e:
                    logger.debug(f"[i] Hub kanalına mesaj gönderilemedi: {e}")

            # Kullanıcıya dönüş mesajı
            remaining = challenge['team_size'] - participant_count
            
            if challenge_started:
                message = f"🎊 *TAKIM DOLDU!* 🚀 Challenge başlatıldı! Kanalınızı kontrol edin."
            elif challenge_start_error:
                message = f"⚠️ *TAKIM DOLDU AMA BAŞLATMA HATASI!* Lütfen admin ile iletişime geçin."
            elif remaining > 0:
                message = f"✅ Katıldınız! 📊 *{participant_count}/{challenge['team_size']}* | ⏳ *{remaining} kişi* daha gerekli"
            else:
                message = f"🎊 *TAKIM DOLDU!* 🚀 Challenge başlatılıyor..."

            return {
                "success": True,
                "message": message,
                "challenge_id": challenge_id,
                "challenge_started": challenge_started
            }

        except Exception as e:
            logger.error(f"[X] ChallengeHubService.join_challenge hatası: {e}", exc_info=True)
            return {
                "success": False,
                "message": "❌ Challenge'a katılırken bir hata oluştu.",
                "error_code": "JOIN_ERROR"
            }

    async def _start_challenge(self, challenge_id: str):
        """
        Challenge'ı başlatır (takım dolduğunda).
        Random tema ve proje seçer, süreyi DB'den alır.
        """
        try:
            import random
            from src.repositories import ChallengeThemeRepository
            
            challenge = self.hub_repo.get(challenge_id)
            if not challenge:
                logger.error(f"[X] Challenge bulunamadı: {challenge_id}")
                raise ValueError(f"Challenge bulunamadı: {challenge_id}")

            # Challenge zaten başlamış mı kontrol et
            if challenge.get("status") == "active":
                logger.warning(f"[!] Challenge zaten aktif: {challenge_id}")
                return

            # 1. Tema belirleme: Önceden seçilmişse onu kullan, değilse random seç
            existing_theme = challenge.get("theme")
            
            if existing_theme and existing_theme != "TBD":
                # Tema zaten seçilmiş, onu kullan
                theme_name = existing_theme
                logger.info(f"[i] Önceden seçilmiş tema kullanılıyor: {theme_name}")
            else:
                # Random tema seç
                if not self.db_client:
                    active_themes = self.theme_repo.get_active_themes()
                else:
                    theme_repo = ChallengeThemeRepository(self.db_client)
                    active_themes = theme_repo.get_active_themes()
                
                if not active_themes:
                    logger.error("[X] Aktif tema bulunamadı")
                    raise ValueError("Aktif tema bulunamadı")
                
                selected_theme = random.choice(active_themes)
                theme_name = selected_theme["name"]
                logger.info(f"[i] Random tema seçildi: {theme_name}")
            
            # 2. Random proje seç (tema bazlı)
            project = self.project_repo.get_random_project(theme_name)
            if not project:
                logger.error(f"[X] Tema için proje bulunamadı: {theme_name}")
                raise ValueError(f"Tema için proje bulunamadı: {theme_name}")

            logger.info(f"[i] Proje seçildi: {project.get('name', 'N/A')}")

            # 3. Süreyi DB'den al (proje bazlı) - Minimum 72 saat
            deadline_hours = project.get("estimated_hours", 48)
            # Minimum süre: 72 saat
            if deadline_hours < 72:
                deadline_hours = 72
                logger.info(f"[i] Süre minimum 72 saate ayarlandı (proje: {deadline_hours} saat < 72)")
            difficulty = project.get("difficulty_level", "intermediate")
            logger.info(f"[i] Süre belirlendi: {deadline_hours} saat | Zorluk: {difficulty}")

            # LLM ile özelleştir
            try:
                enhanced_project = await self.enhancement.enhance_project(
                    base_project=project,
                    team_size=challenge["team_size"],
                    deadline_hours=deadline_hours,
                    theme=theme_name
                )
                logger.info("[+] Proje LLM ile özelleştirildi")
            except Exception as e:
                logger.warning(f"[!] LLM özelleştirme hatası, orijinal proje kullanılıyor: {e}")
                enhanced_project = project

            # 4. Challenge kanalı aç
            channel_suffix = str(uuid.uuid4())[:8]
            channel_name = f"challenge-{theme_name.lower().replace(' ', '-').replace('_', '-')}-{channel_suffix}"
            
            try:
                challenge_channel = self.conv.create_channel(
                    name=channel_name,
                    is_private=True
                )
                challenge_channel_id = challenge_channel["id"]
                logger.info(f"[+] Challenge kanalı oluşturuldu: #{channel_name} (ID: {challenge_channel_id})")
            except Exception as e:
                logger.error(f"[X] Challenge kanalı oluşturulamadı: {e}", exc_info=True)
                raise

            # 5. Katılımcıları ve owner'ı kanala ekle (önce kullanıcıları ekle, sonra topic ayarla)
            participants = self.participant_repo.get_team_members(challenge_id)
            user_ids = [p["user_id"] for p in participants]
            
            # Owner'ı ekle (creator_id)
            creator_id = challenge.get("creator_id")
            if creator_id and creator_id not in user_ids:
                user_ids.append(creator_id)
            
            logger.info(f"[i] Kanal davet listesi: {len(user_ids)} kullanıcı")
            
            # User token ile oluşturulan kanal olduğu için user token kullanılacak (otomatik)
            try:
                self.conv.invite_users(challenge_channel_id, user_ids)
                logger.info(f"[+] {len(user_ids)} kullanıcı challenge kanalına davet edildi")
            except Exception as e:
                logger.warning(f"[!] Kullanıcılar kanala davet edilirken hata (devam ediliyor): {e}")

            # 6. Kanal topic ve purpose'unu ayarla (kullanıcılar davet edildikten sonra - kanal hazır olacak)
            try:
                import time
                # Kısa bir gecikme ekle (kanalın tam olarak hazır olması için)
                time.sleep(1)
                
                topic_text = f"Challenge: {project.get('name', 'Proje')} | Süre: {deadline_hours} saat | ⚠️ Lütfen kanala başka kişileri davet etmeyin"
                purpose_text = f"Challenge kanalı - {theme_name} teması | Takım: {challenge['team_size'] + 1} kişi | Bu kanal sadece challenge takımı için oluşturulmuştur. Lütfen kanala başka kişileri davet etmeyin."
                
                topic_success = self.conv.set_topic(challenge_channel_id, topic_text)
                purpose_success = self.conv.set_purpose(challenge_channel_id, purpose_text)
                
                if topic_success and purpose_success:
                    logger.info(f"[+] Kanal topic ve purpose ayarlandı: {challenge_channel_id}")
                else:
                    logger.warning(f"[!] Kanal topic/purpose ayarlanamadı (non-critical): {challenge_channel_id}")
            except Exception as e:
                # Topic/purpose ayarlanmasa bile challenge devam edebilir
                logger.warning(f"[!] Kanal topic/purpose ayarlanırken hata (devam ediliyor): {e}")

            # 7. Challenge'ı güncelle
            deadline = datetime.now() + timedelta(hours=deadline_hours)
            update_data = {
                "status": "active",
                "theme": theme_name,
                "challenge_channel_id": challenge_channel_id,
                "selected_project_id": project["id"],
                # Canvas/özet için gerekli temel proje bilgileri
                "project_name": project.get("name"),
                "project_description": project.get("description"),
                "deadline_hours": deadline_hours,
                "difficulty": difficulty,
                "llm_customizations": json.dumps(enhanced_project.get("llm_enhanced_features", [])),
                "started_at": datetime.now().isoformat(),
                "deadline": deadline.isoformat()
            }
            
            self.hub_repo.update(challenge_id, update_data)
            logger.info(f"[+] Challenge güncellendi: {challenge_id}")

            # 7.1. Duyuru kanalında challenge özeti/canvas mesajını oluştur veya güncelle
            try:
                if self.evaluation_service:
                    # Evaluation servisi, hub + evaluation + github bilgilerini birleştirerek
                    # duyuru kanalındaki özet mesajı güncelleyecek.
                    await self.evaluation_service.update_challenge_canvas(challenge_id)
            except Exception as e:
                logger.warning(f"[!] Challenge canvas/özet mesajı güncellenemedi: {e}")

            # 8. Challenge içeriğini kanala gönder
            try:
                await self._post_challenge_content(challenge_channel_id, enhanced_project, challenge, theme_name, deadline_hours)
                logger.info(f"[+] Challenge içeriği kanala gönderildi: {challenge_channel_id}")
            except Exception as e:
                logger.error(f"[X] Challenge içeriği gönderilemedi: {e}", exc_info=True)
                # İçerik gönderilemese bile devam et

            # 9. Deadline sonrası kapatma görevi planla
            try:
                self.cron.add_once_job(
                    func=self._close_challenge,
                    delay_minutes=deadline_hours * 60,
                    job_id=f"close_challenge_{challenge_id}",
                    args=[challenge_id, challenge_channel_id]
                )
                logger.info(f"[+] Challenge kapatma görevi planlandı: {deadline_hours} saat sonra")
            except Exception as e:
                logger.warning(f"[!] Challenge kapatma görevi planlanamadı: {e}")

            # 10. Challenge başlatıldıktan sonra hemen yetkisiz kullanıcı kontrolü yap
            try:
                import time
                time.sleep(5)  # Kullanıcıların kanala eklenmesi ve Slack'in senkronize olması için bekleme
                self.monitor_challenge_channels()
                logger.info(f"[+] Challenge kanalı kontrol edildi: {challenge_channel_id}")
            except Exception as e:
                logger.warning(f"[!] Challenge kanalı kontrol edilemedi: {e}")

            logger.info(f"[+] Challenge başarıyla başlatıldı | ID: {challenge_id} | Tema: {theme_name} | Kanal: {challenge_channel_id}")

        except Exception as e:
            logger.error(f"[X] ChallengeHubService._start_challenge hatası: {e}", exc_info=True)
            # Hata durumunda challenge durumunu "failed" olarak işaretle
            try:
                self.hub_repo.update(challenge_id, {"status": "failed"})
            except:
                pass
            raise

    async def _post_challenge_content(
        self,
        channel_id: str,
        project: Dict,
        challenge: Dict,
        theme_name: str,
        deadline_hours: int
    ):
        """
        Challenge içeriğini kanala gönderir - Önce açıklama, sonra proje detayları.
        """
        try:
            # 1. Karşılama ve Temel Bilgiler (Kısa ve net)
            intro_blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🚀 Challenge Başladı!",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*⏱️ Süre:*\n{deadline_hours} saat"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*👥 Takım:*\n{challenge['team_size'] + 1} kişi"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*🎯 Hedef:*\nProjeyi tamamla!"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*📅 Bitiş:*\n{(datetime.now() + timedelta(hours=deadline_hours)).strftime('%d.%m %H:%M')}"
                        }
                    ]
                },
                {"type": "divider"}
            ]
            
            # İlk mesajı gönder
            self.chat.post_message(
                channel=channel_id,
                text="🚀 Challenge başladı!",
                blocks=intro_blocks
            )
            
            # 2. Proje Detayları (Sadece önemli bilgiler)
            project_blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"📋 {project.get('name', 'Proje')}",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Açıklama:*\n{project.get('description', '')}"
                    }
                },
                {"type": "divider"}
            ]
            
            # Başarı kriterleri (en önemli 5 tanesi)
            objectives = project.get("objectives", [])
            if isinstance(objectives, str):
                try:
                    objectives = json.loads(objectives)
                except:
                    objectives = []
            
            if objectives:
                obj_text = "*✅ Yapılması Gerekenler:*\n\n"
                for i, obj in enumerate(objectives[:5], 1):
                    obj_text += f"{i}. {obj}\n"
                
                project_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": obj_text
                    }
                })
                project_blocks.append({"type": "divider"})

            # Görevler (Sadece başlıklar, detaysız - en fazla 5 tanesi)
            tasks = project.get("tasks", [])
            if isinstance(tasks, str):
                try:
                    tasks = json.loads(tasks)
                except:
                    tasks = []

            if tasks:
                task_text = "*📋 Görevler:*\n\n"
                for i, task in enumerate(tasks[:5], 1):
                    task_title = task.get("title", task.get("name", f"Görev {i}"))
                    task_hours = task.get("estimated_hours", "?")
                    task_text += f"{i}. {task_title} (⏱️ ~{task_hours}h)\n"
                
                project_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": task_text
                    }
                })
                project_blocks.append({"type": "divider"})

            # Tek satırda önemli bilgiler
            project_blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"📌 *Zorluk:* {project.get('difficulty_level', 'intermediate').capitalize()} | "
                        f"*Tema:* {self._get_theme_icon(theme_name)} {theme_name}"
                    )
                }
            })

            # İkinci mesajı gönder
            self.chat.post_message(
                channel=channel_id,
                text=f"📋 Proje: {project.get('name', 'Proje')}",
                blocks=project_blocks
            )
            
            # 3. Önemli Kurallar (Kısa)
            rules_blocks = [
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "📌 *Önemli Bilgiler*\n\n"
                            "⚠️ Bu kanal sadece takım içindir - başkalarını davet etmeyin\n"
                            "💬 Sorularınızı ve ilerlemenizi bu kanalda paylaşın\n"
                            "🎯 Bitirmek için: `/challenge finish` komutunu kullanın\n\n"
                            "Başarılar! 🚀"
                        )
                    }
                }
            ]
            
            # Üçüncü mesajı gönder
            self.chat.post_message(
                channel=channel_id,
                text="📌 Kanal kuralları ve önemli bilgiler",
                blocks=rules_blocks
            )

        except Exception as e:
            logger.error(f"[X] Challenge içeriği gönderme hatası: {e}", exc_info=True)

    async def _close_challenge(self, challenge_id: str, channel_id: str):
        """
        Challenge'ı kapatır (deadline sonrası).
        """
        try:
            # Challenge bilgisini al
            challenge = self.hub_repo.get(challenge_id)
            if not challenge:
                logger.error(f"[X] Challenge bulunamadı: {challenge_id}")
                return
            
            # Başlangıçta temel verileri hazırla
            update_data = {
                "ended_at": datetime.now().isoformat()
            }
            
            # Tüm katılımcıların istatistiklerini güncelle (creator + participants)
            try:
                # Creator'ı ekle
                creator_id = challenge.get("creator_id")
                if creator_id:
                    self.stats_repo.increment_completed(creator_id)
                    logger.debug(f"[i] Creator istatistiği güncellendi: {creator_id}")
                
                # Tüm katılımcıları ekle
                participants = self.participant_repo.get_team_members(challenge_id)
                for participant in participants:
                    user_id = participant.get("user_id")
                    if user_id:
                        self.stats_repo.increment_completed(user_id)
                        logger.debug(f"[i] Katılımcı istatistiği güncellendi: {user_id}")
                
                logger.info(f"[+] {len(participants) + (1 if creator_id else 0)} kullanıcının istatistiği güncellendi | Challenge: {challenge_id}")
            except Exception as e:
                logger.warning(f"[!] İstatistik güncelleme hatası: {e}")
            
            # Değerlendirme başlat (KANAL ARŞİVLENMEDEN ÖNCE - mesaj göndermek için)
            evaluation_started = False
            evaluation_channel_id = None
            if self.evaluation_service:
                try:
                    eval_result = await self.evaluation_service.start_evaluation(challenge_id, channel_id)
                    logger.info(f"[+] Değerlendirme başlatıldı | Challenge: {challenge_id}")
                    
                    if eval_result.get("success"):
                        evaluation_started = True
                        evaluation_channel_id = eval_result.get("evaluation_channel_id")
                        
                        # Challenge kanalına veda ve yönlendirme mesajı at
                        if evaluation_channel_id:
                            self.chat.post_message(
                                channel=channel_id,
                                text=f"🚀 Challenge tamamlandı! Değerlendirme süreci için <#{evaluation_channel_id}> kanalına geçiyoruz...",
                                blocks=[
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": (
                                                f"🎊 *Tebrikler Ekip! Challenge Tamamlandı!* 🎊\n\n"
                                                f"Süreç artık *Değerlendirme Aşamasına* geçti.\n"
                                                f"Tüm ekip üyeleri otomatik olarak yeni kanala taşınıyor: <#{evaluation_channel_id}>\n\n"
                                                f"⏳ *Önemli:* Bu kanal *4 saat sonra* otomatik olarak arşivlenecektir. Bu süre zarfında değerlendirme kanalına geçiş yapabilir ve eski mesajları kontrol edebilirsiniz. 👋"
                                            )
                                        }
                                    }
                                ]
                            )
                except Exception as e:
                    logger.warning(f"[!] Değerlendirme başlatılamadı: {e}")
            
            # Challenge Status'unu GÜNCELLE
            if evaluation_started:
                update_data["status"] = "evaluating"
            else:
                update_data["status"] = "completed"
                update_data["completed_at"] = datetime.now().isoformat()
            
            try:
                self.hub_repo.update(challenge_id, update_data)
                logger.info(f"[+] Challenge status güncellendi: {challenge_id} | Status: {update_data['status']}")
            except Exception as e:
                logger.error(f"[X] Challenge status güncellenemedi: {e}")

            # Kanalı 4 saat sonra arşivlemek üzere planla
            try:
                delay_hours = 4
                self.cron.add_once_job(
                    func=self._archive_channel_delayed,
                    delay_minutes=delay_hours * 60,
                    job_id=f"archive_challenge_{challenge_id}",
                    args=[challenge_id, channel_id]
                )
                logger.info(f"[+] Challenge kanalı 4 saat sonra arşivlenmek üzere planlandı | ID: {challenge_id}")
            except Exception as e:
                logger.warning(f"[!] Challenge kanalı arşivleme görevi planlanamadı: {e}")
            
            logger.info(f"[+] Challenge kapatıldı | ID: {challenge_id}")
        except Exception as e:
            logger.error(f"[X] Challenge kapatma hatası: {e}", exc_info=True)

    def _archive_channel_delayed(self, challenge_id: str, channel_id: str):
        """Kanalı gecikmeli olarak arşivler (Cron tarafından çağrılır)."""
        try:
            success = self.conv.archive_channel(channel_id)
            if success:
                logger.info(f"[+] Kanal başarıyla arşivlendi: {channel_id} | Challenge: {challenge_id}")
            else:
                logger.warning(f"[!] Kanal arşivlenemedi: {channel_id} | Challenge: {challenge_id}")
        except Exception as e:
            logger.error(f"[X] Gecikmeli kanal arşivleme hatası: {e} | Kanal: {channel_id}")

    async def leave_challenge(self, user_id: str, challenge_id: str) -> Dict[str, Any]:
        """
        Kullanıcının bir challenge'dan ayrılmasını sağlar (Sadece recruiting durumunda).
        """
        try:
            challenge = self.hub_repo.get(challenge_id)
            if not challenge:
                return {"success": False, "message": "❌ Challenge bulunamadı."}

            if challenge.get("status") != "recruiting":
                return {"success": False, "message": "❌ Sadece katılım aşamasındaki challenge'lardan ayrılabilirsiniz."}

            # Katılımcı mı kontrol et
            participant = self.participant_repo.get_by_challenge_and_user(challenge_id, user_id)
            if not participant:
                return {"success": False, "message": "❌ Bu challenge'ın bir parçası değilsiniz."}

            # Sahibi mı kontrol et
            is_owner = challenge.get("creator_id") == user_id

            if is_owner:
                # Sahibi ayrılırsa challenge iptal edilir
                self.hub_repo.update(challenge_id, {"status": "cancelled", "ended_at": datetime.now().isoformat()})
                logger.info(f"[-] Challenge iptal edildi (sahibi ayrıldı) | ID: {challenge_id}")
                message = "📉 Challenge sahibi ayrıldığı için challenge iptal edildi."
            else:
                # Normal katılımcı ayrılırsa sadece katılımcı silinir
                self.participant_repo.delete(participant["id"])
                logger.info(f"[-] Kullanıcı challenge'dan ayrıldı: {user_id} | ID: {challenge_id}")
                message = "✅ Challenge'dan başarıyla ayrıldınız."

            # Hub kanalına güncelleme gönder
            hub_channel_id = challenge.get("hub_channel_id")
            if hub_channel_id:
                try:
                    if is_owner:
                        self.chat.post_message(channel=hub_channel_id, text=f"📉 Bir challenge sahibi tarafından iptal edildi.")
                    else:
                        updated_participants = self.participant_repo.get_team_members(challenge_id)
                        count = len(updated_participants)
                        self.chat.post_message(channel=hub_channel_id, text=f"🏃 Bir katılımcı ayrıldı. 📊 *{count}/{challenge['team_size']}*")
                except:
                    pass

            return {"success": True, "message": message}

        except Exception as e:
            logger.error(f"[X] leave_challenge hatası: {e}", exc_info=True)
            return {"success": False, "message": "❌ Ayrılma işlemi sırasında bir hata oluştu."}

    async def monitor_recruitment_timeouts(self):
        """
        Uzun süre recruiting aşamasında kalan challenge'ları otomatik iptal eder.
        """
        try:
            # 7 günden eski recruiting challenge'ları bul
            timeout_date = (datetime.now() - timedelta(days=7)).isoformat()
            
            recruiting_challenges = self.hub_repo.list(filters={"status": "recruiting"})
            
            cancelled_count = 0
            for challenge in recruiting_challenges:
                created_at = challenge.get("created_at")
                if created_at and created_at < timeout_date:
                    self.hub_repo.update(challenge["id"], {
                        "status": "failed",
                        "ended_at": datetime.now().isoformat()
                    })
                    cancelled_count += 1
                    logger.info(f"[i] Challenge zaman aşımından dolayı iptal edildi: {challenge['id']}")
                    
                    hub_channel = challenge.get("hub_channel_id")
                    if hub_channel:
                        self.chat.post_message(
                            channel=hub_channel,
                            text=f"⏰ Bir challenge yeterli katılımcı sayısına ulaşamadığı için zaman aşımı nedeniyle iptal edildi."
                        )
            
            if cancelled_count > 0:
                logger.info(f"[+] Toplam {cancelled_count} challenge zaman aşımına uğratıldı.")
                
        except Exception as e:
            logger.error(f"[X] recruitment_timeouts izleme hatası: {e}")

    async def request_finish_challenge(self, challenge_id: str, requester_id: str, channel_id: str) -> Dict[str, Any]:
        """
        Challenge bitirme isteğini işler. Doğrudan bitirmez, admine onay gönderir.
        """
        try:
            challenge = self.hub_repo.get(challenge_id)
            if not challenge:
                return {"success": False, "message": "❌ Challenge bulunamadı."}

            if challenge.get("status") != "active":
                return {"success": False, "message": f"❌ Challenge zaten {challenge.get('status')} durumunda."}

            # İsteyen kullanıcının bilgisini al
            try:
                user_info = self.chat.client.users_info(user=requester_id)
                requester_name = user_info["user"]["real_name"]
            except:
                requester_name = requester_id

            # Admin kanalını bul
            settings = get_settings()
            admin_channel = settings.admin_channel_id

            if not admin_channel:
                logger.error("[X] Admin kanalı (ADMIN_CHANNEL_ID) yapılandırılmamış!")
                return {
                    "success": False, 
                    "message": "❌ Sistem hatası: Admin kanalı bulunamadı. Lütfen yetkiliye bildirin."
                }

            # Admine sadeleştirilmiş onay mesajı gönder
            admin_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"🛑 *Challenge Bitirme İsteği*\n"
                            f"📣 İsteyen: *{requester_name}* | 🆔 Challenge: `{challenge_id[:8]}`"
                        )
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "✅ Onayla",
                                "emoji": True
                            },
                            "style": "primary",
                            "action_id": "admin_approve_finish_challenge",
                            "value": f"{challenge_id}|{channel_id}|{requester_id}"
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "❌ Reddet",
                                "emoji": True
                            },
                            "style": "danger",
                            "action_id": "admin_reject_finish_challenge",
                            "value": f"{challenge_id}|{channel_id}|{requester_id}"
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "🔍 Detaylar",
                                "emoji": True
                            },
                            "action_id": "admin_finish_details",
                            "value": f"{challenge_id}|{channel_id}|{requester_id}"
                        }
                    ]
                }
            ]

            self.chat.post_message(
                channel=admin_channel,
                text=f"🛑 Challenge Bitirme İsteği: {challenge_id[:8]}",
                blocks=admin_blocks
            )

            # Kullanıcıya bilgi ver
            return {
                "success": True,
                "message": "✅ Challenge bitirme isteğiniz Akademi Yönetimine iletildi. Onaylandığında işlem tamamlanacaktır."
            }

        except Exception as e:
            logger.error(f"[X] Challenge bitirme isteği hatası: {e}", exc_info=True)
            return {"success": False, "message": "❌ İstek oluşturulurken hata oluştu."}

    def _get_hub_channel(self) -> Optional[str]:
        """#challenge-hub kanalını bulur."""
        from src.core.settings import get_settings
        settings = get_settings()
        # Settings'den al veya varsayılan olarak None döndür
        # Kullanıcı #challenge-hub kanalını manuel oluşturmalı
        return None

    def check_and_remove_unauthorized_user(self, channel_id: str, user_id: str) -> Dict[str, Any]:
        """
        Challenge kanalına yetkisiz kullanıcı katıldığında çağrılır.
        Kullanıcı yetkisiz ise kanaldan çıkarır ve uyarı gönderir.
        """
        try:
            # 1. Bu kanal bir challenge kanalı mı?
            challenge = self.hub_repo.get_by_channel_id(channel_id)
            if not challenge:
                # Bu bir challenge kanalı değil, işlem yapma
                return {"is_challenge_channel": False, "action": "none"}
            
            # 2. Challenge'ın yetkili kullanıcılarını al
            authorized_users = set()
            
            # Creator'ı ekle
            creator_id = challenge.get("creator_id")
            if creator_id:
                authorized_users.add(creator_id)
            
            # Participants'ları ekle
            participants = self.participant_repo.get_team_members(challenge["id"])
            for participant in participants:
                authorized_users.add(participant["user_id"])
            
            # 3. Bot'u da ekle (bot her zaman kanalda olmalı)
            try:
                bot_info = self.chat.client.auth_test()
                if bot_info["ok"]:
                    bot_user_id = bot_info["user_id"]
                    authorized_users.add(bot_user_id)
            except Exception as e:
                logger.warning(f"[!] Bot user ID alınamadı: {e}")
            
            # 4. User token sahibini ekle (workspace admin - kanalı oluşturan)
            # User token sahibi kendisini çıkaramaz (cant_kick_self hatası)
            try:
                if self.conv.user_client:
                    user_token_info = self.conv.user_client.auth_test()
                    if user_token_info["ok"]:
                        user_token_owner_id = user_token_info["user_id"]
                        authorized_users.add(user_token_owner_id)
                        logger.debug(f"[i] User token sahibi yetkili kullanıcılara eklendi: {user_token_owner_id}")
            except Exception as e:
                logger.warning(f"[!] User token sahibi bilgisi alınamadı: {e}")
            
            # 5. Kullanıcı yetkili mi?
            if user_id in authorized_users:
                # Yetkili kullanıcı, işlem yapma
                logger.debug(f"[i] Yetkili kullanıcı kanala katıldı: {user_id} | Challenge: {challenge['id']}")
                return {"is_challenge_channel": True, "is_authorized": True, "action": "none"}
            
            # 6. Yetkisiz kullanıcı - kanaldan çıkar
            logger.warning(f"[!] Yetkisiz kullanıcı challenge kanalına katılmaya çalıştı: {user_id} | Challenge: {challenge['id']} | Kanal: {channel_id}")
            logger.info(f"[i] Yetkili kullanıcılar: {authorized_users}")
            
            try:
                # Kullanıcıyı kanaldan çıkar
                logger.info(f"[>] Kullanıcı kanaldan çıkarılıyor: {user_id} | Kanal: {channel_id}")
                try:
                    success = self.conv.kick_user(channel_id, user_id)
                    logger.info(f"[i] kick_user sonucu: {success}")
                except Exception as kick_error:
                    logger.error(f"[X] kick_user exception: {kick_error} | Kullanıcı: {user_id} | Kanal: {channel_id}", exc_info=True)
                    success = False
                
                if success:
                    logger.info(f"[+] Yetkisiz kullanıcı kanaldan çıkarıldı: {user_id} | Challenge: {challenge['id']}")
                    
                    # Kullanıcıya DM ile uyarı gönder
                    try:
                        dm_channel = self.conv.open_conversation([user_id])
                        if dm_channel and dm_channel.get("channel"):
                            dm_id = dm_channel["channel"]["id"]
                            self.chat.post_message(
                                channel=dm_id,
                                text=(
                                    "⚠️ *Yetkisiz Kanal Erişimi*\n\n"
                                    "Challenge kanalları sadece challenge takımı için oluşturulmuştur. "
                                    "Bu kanala katılamazsınız çünkü bu challenge'ın takım üyesi değilsiniz.\n\n"
                                    "💡 *Not:* Challenge kanallarına sadece challenge sahibi ve takım üyeleri katılabilir. "
                                    "Lütfen başka challenge kanallarına katılmaya çalışmayın."
                                ),
                                blocks=[{
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": (
                                            "⚠️ *Yetkisiz Kanal Erişimi*\n\n"
                                            "Challenge kanalları sadece challenge takımı için oluşturulmuştur. "
                                            "Bu kanala katılamazsınız çünkü bu challenge'ın takım üyesi değilsiniz.\n\n"
                                            "💡 *Not:* Challenge kanallarına sadece challenge sahibi ve takım üyeleri katılabilir. "
                                            "Lütfen başka challenge kanallarına katılmaya çalışmayın."
                                        )
                                    }
                                }]
                            )
                    except Exception as e:
                        logger.warning(f"[!] DM gönderilemedi: {e}")
                    
                    # Challenge kanalına bilgilendirme mesajı gönder
                    try:
                        self.chat.post_message(
                            channel=channel_id,
                            text=(
                                f"⚠️ *Yetkisiz Kullanıcı Tespit Edildi*\n\n"
                                f"<@{user_id}> bu kanala yetkisiz olarak katılmaya çalıştı ve otomatik olarak çıkarıldı.\n\n"
                                f"💡 *Hatırlatma:* Bu kanal sadece challenge takımı için oluşturulmuştur. "
                                f"Lütfen kanala başka kişileri davet etmeyin."
                            ),
                            blocks=[{
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"⚠️ *Yetkisiz Kullanıcı Tespit Edildi*\n\n"
                                        f"<@{user_id}> bu kanala yetkisiz olarak katılmaya çalıştı ve otomatik olarak çıkarıldı.\n\n"
                                        f"💡 *Hatırlatma:* Bu kanal sadece challenge takımı için oluşturulmuştur. "
                                        f"Lütfen kanala başka kişileri davet etmeyin."
                                    )
                                }
                            }]
                        )
                    except Exception as e:
                        logger.warning(f"[!] Challenge kanalına bilgilendirme mesajı gönderilemedi: {e}")
                    
                    return {
                        "is_challenge_channel": True,
                        "is_authorized": False,
                        "action": "removed",
                        "user_id": user_id,
                        "challenge_id": challenge["id"]
                    }
                else:
                    logger.error(f"[X] Kullanıcı kanaldan çıkarılamadı: {user_id} | Kanal: {channel_id} | Challenge: {challenge['id']}")
                    
                    # Admin'e bildirim gönder
                    try:
                        from src.core.settings import get_settings
                        settings = get_settings()
                        admin_channel = settings.admin_channel_id
                        
                        if admin_channel:
                            self.chat.post_message(
                                channel=admin_channel,
                                text=(
                                    f"⚠️ *Yetkisiz Kullanıcı Çıkarılamadı*\n\n"
                                    f"Kullanıcı: <@{user_id}>\n"
                                    f"Challenge: `{challenge['id'][:8]}...`\n"
                                    f"Kanal: <#{channel_id}>\n\n"
                                    f"❌ Kullanıcı otomatik olarak çıkarılamadı. Lütfen manuel olarak çıkarın.\n\n"
                                    f"💡 *Not:* Bot'un `groups:write` ve `channels:write` scope'larına sahip olduğundan emin olun."
                                ),
                                blocks=[{
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": (
                                            f"⚠️ *Yetkisiz Kullanıcı Çıkarılamadı*\n\n"
                                            f"Kullanıcı: <@{user_id}>\n"
                                            f"Challenge: `{challenge['id'][:8]}...`\n"
                                            f"Kanal: <#{channel_id}>\n\n"
                                            f"❌ Kullanıcı otomatik olarak çıkarılamadı. Lütfen manuel olarak çıkarın.\n\n"
                                            f"💡 *Not:* Bot'un `groups:write` ve `channels:write` scope'larına sahip olduğundan emin olun."
                                        )
                                    }
                                }]
                            )
                    except Exception as admin_error:
                        logger.warning(f"[!] Admin'e bildirim gönderilemedi: {admin_error}")
                    
                    return {
                        "is_challenge_channel": True,
                        "is_authorized": False,
                        "action": "failed_to_remove",
                        "user_id": user_id,
                        "challenge_id": challenge["id"]
                    }
            except Exception as e:
                logger.error(f"[X] Kullanıcı kanaldan çıkarılırken hata: {e}", exc_info=True)
                return {
                    "is_challenge_channel": True,
                    "is_authorized": False,
                    "action": "error",
                    "error": str(e)
                }
                
        except Exception as e:
            logger.error(f"[X] Yetkisiz kullanıcı kontrolü hatası: {e}", exc_info=True)
            return {"is_challenge_channel": False, "action": "error", "error": str(e)}

    def monitor_challenge_channels(self):
        """
        Tüm aktif challenge kanallarını periyodik olarak kontrol eder.
        Yetkisiz kullanıcıları tespit edip çıkarır.
        """
        try:
            # Aktif challenge'ları al
            active_challenges = self.hub_repo.get_all_active()
            
            if not active_challenges:
                logger.debug("[i] Aktif challenge yok, kontrol atlandı")
                return
            
            logger.info(f"[>] Challenge kanalları kontrol ediliyor: {len(active_challenges)} aktif challenge")
            
            for challenge in active_challenges:
                channel_id = challenge.get("challenge_channel_id")
                if not channel_id:
                    continue
                
                try:
                    # Kanal üyelerini al
                    channel_members = set(self.conv.get_members(channel_id))
                    
                    # Yetkili kullanıcıları belirle
                    authorized_users = set()
                    
                    # Creator'ı ekle
                    creator_id = challenge.get("creator_id")
                    if creator_id:
                        authorized_users.add(creator_id)
                    
                    # Participants'ları ekle
                    participants = self.participant_repo.get_team_members(challenge["id"])
                    for participant in participants:
                        authorized_users.add(participant["user_id"])
                    
                    # Bot'u ekle
                    try:
                        bot_info = self.chat.client.auth_test()
                        if bot_info["ok"]:
                            bot_user_id = bot_info["user_id"]
                            authorized_users.add(bot_user_id)
                    except Exception:
                        pass
                    
                    # User token sahibini ekle
                    try:
                        if self.conv.user_client:
                            user_token_info = self.conv.user_client.auth_test()
                            if user_token_info["ok"]:
                                user_token_owner_id = user_token_info["user_id"]
                                authorized_users.add(user_token_owner_id)
                    except Exception:
                        pass
                    
                    # Yetkisiz kullanıcıları bul
                    unauthorized_users = channel_members - authorized_users
                    
                    if unauthorized_users:
                        logger.warning(f"[!] Yetkisiz kullanıcılar tespit edildi: {len(unauthorized_users)} kişi | Challenge: {challenge['id']} | Kanal: {channel_id}")
                        
                        # Her yetkisiz kullanıcıyı çıkar (rate limit için aralarına gecikme ekle)
                        import time
                        for i, user_id in enumerate(unauthorized_users):
                            try:
                                result = self.check_and_remove_unauthorized_user(channel_id, user_id)
                                if result.get("action") == "removed":
                                    logger.info(f"[+] Yetkisiz kullanıcı çıkarıldı: {user_id} | Challenge: {challenge['id']}")
                                
                                # Rate limit'e takılmamak için her işlem arasında kısa gecikme (son kullanıcıdan sonra bekleme yok)
                                if i < len(unauthorized_users) - 1:
                                    time.sleep(2)  # 2 saniye bekle (dakikada ~20 request için güvenli)
                            except Exception as e:
                                logger.error(f"[X] Kullanıcı çıkarılırken hata: {user_id} | {e}")
                    else:
                        logger.debug(f"[i] Challenge kanalı temiz: {challenge['id']} | Kanal: {channel_id}")
                        
                except Exception as e:
                    logger.warning(f"[!] Challenge kanalı kontrol edilemedi: {challenge['id']} | {e}")
                    
        except Exception as e:
            logger.error(f"[X] Challenge kanalları kontrol hatası: {e}", exc_info=True)

    def _get_theme_icon(self, theme: str) -> str:
        """Tema için icon döndürür."""
        icons = {
            "AI Chatbot": "🤖",
            "Web App": "🌐",
            "Data Analysis": "📊",
            "Mobile App": "📱",
            "Automation": "⚙️"
        }
        return icons.get(theme, "🎯")
