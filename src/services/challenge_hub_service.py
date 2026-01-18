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
        cron_client: CronClient
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

    async def start_challenge(
        self,
        creator_id: str,
        theme: str,
        team_size: int,
        deadline_hours: int = 48,
        difficulty: str = "intermediate"
    ) -> Dict[str, Any]:
        """
        Yeni challenge başlatır.
        """
        try:
            # 1. Kullanıcının aktif challenge'ı var mı?
            active_challenges = self.participant_repo.get_user_active_challenges(creator_id)
            if active_challenges:
                return {
                    "success": False,
                    "message": f"❌ Zaten aktif bir challenge'ınız var. Önce onu tamamlayın.",
                    "error_code": "USER_HAS_ACTIVE_CHALLENGE"
                }

            # 2. Challenge hub oluştur
            challenge_id = str(uuid.uuid4())
            deadline = datetime.now() + timedelta(hours=deadline_hours)

            hub_data = {
                "id": challenge_id,
                "creator_id": creator_id,
                "theme": theme,
                "team_size": team_size,
                "status": "recruiting",
                "deadline_hours": deadline_hours,
                "difficulty": difficulty,
                "deadline": deadline.isoformat()
            }

            self.hub_repo.create(hub_data)

            # 3. Creator'ı otomatik ekle
            self.participant_repo.create({
                "id": str(uuid.uuid4()),
                "challenge_hub_id": challenge_id,
                "user_id": creator_id,
                "role": "leader"
            })

            # 4. #challenge-hub kanalına mesaj gönder
            hub_channel = self._get_hub_channel()
            if hub_channel:
                message = (
                    f"🔥 *Yeni Challenge Açıldı!*\n\n"
                    f"Tema: {self._get_theme_icon(theme)} {theme}\n"
                    f"Takım: {team_size} kişi\n"
                    f"Süre: {deadline_hours} saat\n"
                    f"Zorluk: {difficulty.capitalize()}\n\n"
                    f"Katılmak isteyenler:\n"
                    f"👉 `/challenge join`"
                )
                self.chat.post_message(channel=hub_channel, text=message)

            logger.info(f"[+] Challenge başlatıldı | ID: {challenge_id} | Tema: {theme} | Takım: {team_size}")

            return {
                "success": True,
                "challenge_id": challenge_id,
                "message": f"✅ Challenge başlatıldı! ({1}/{team_size} kişi)"
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

            # 2. Kullanıcı zaten katılmış mı?
            existing = self.participant_repo.get_by_challenge_and_user(challenge_id, user_id)
            if existing:
                return {
                    "success": False,
                    "message": "❌ Zaten bu challenge'a katıldınız. Aynı challenge'a iki kez katılamazsınız.",
                    "error_code": "ALREADY_PARTICIPATING"
                }

            # 3. Challenge durumu kontrolü
            if challenge["status"] != "recruiting":
                return {
                    "success": False,
                    "message": "❌ Bu challenge'a katılım kabul edilmiyor (dolu veya başlamış).",
                    "error_code": "CHALLENGE_NOT_RECRUITING"
                }

            # 4. Takım dolu mu?
            current_participants = self.participant_repo.get_team_members(challenge_id)
            if len(current_participants) >= challenge["team_size"]:
                return {
                    "success": False,
                    "message": "❌ Bu challenge'ın takımı dolmuş.",
                    "error_code": "TEAM_FULL"
                }

            # 5. Kullanıcının başka aktif challenge'ı var mı?
            active_challenges = self.participant_repo.get_user_active_challenges(user_id)
            if active_challenges and active_challenges[0]["id"] != challenge_id:
                return {
                    "success": False,
                    "message": f"❌ Zaten aktif bir challenge'ınız var. Önce onu tamamlayın.",
                    "error_code": "USER_HAS_ACTIVE_CHALLENGE"
                }

            # 6. Katılımcı ekle
            self.participant_repo.create({
                "id": str(uuid.uuid4()),
                "challenge_hub_id": challenge_id,
                "user_id": user_id,
                "role": "member"
            })

            # 7. Takım doldu mu kontrol et
            updated_participants = self.participant_repo.get_team_members(challenge_id)
            participant_count = len(updated_participants)

            # Hub kanalına güncelleme
            hub_channel = self._get_hub_channel()
            if hub_channel:
                self.chat.post_message(
                    channel=hub_channel,
                    text=f"✅ Yeni katılımcı! ({participant_count}/{challenge['team_size']} kişi)"
                )

            # 8. Takım dolduysa challenge'ı başlat
            if participant_count >= challenge["team_size"]:
                await self._start_challenge(challenge_id)

            return {
                "success": True,
                "message": f"✅ Challenge'a katıldınız! ({participant_count}/{challenge['team_size']} kişi)",
                "challenge_id": challenge_id
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
        """
        try:
            challenge = self.hub_repo.get(challenge_id)
            if not challenge:
                return

            # 1. Proje seç ve özelleştir
            project = self.project_repo.get_random_project(challenge["theme"])
            if not project:
                logger.error(f"[X] Tema için proje bulunamadı: {challenge['theme']}")
                return

            # LLM ile özelleştir
            enhanced_project = await self.enhancement.enhance_project(
                base_project=project,
                team_size=challenge["team_size"],
                deadline_hours=challenge["deadline_hours"],
                theme=challenge["theme"]
            )

            # 2. Challenge kanalı aç
            channel_suffix = str(uuid.uuid4())[:8]
            channel_name = f"challenge-{challenge['theme'].lower().replace(' ', '-')}-{channel_suffix}"
            
            challenge_channel = self.conv.create_channel(
                name=channel_name,
                is_private=True
            )
            challenge_channel_id = challenge_channel["id"]

            # 3. Katılımcıları kanala ekle
            participants = self.participant_repo.get_team_members(challenge_id)
            user_ids = [p["user_id"] for p in participants]
            self.conv.invite_users(challenge_channel_id, user_ids)

            # 4. Challenge'ı güncelle
            deadline = datetime.now() + timedelta(hours=challenge["deadline_hours"])
            self.hub_repo.update(challenge_id, {
                "status": "active",
                "challenge_channel_id": challenge_channel_id,
                "selected_project_id": project["id"],
                "llm_customizations": json.dumps(enhanced_project.get("llm_enhanced_features", [])),
                "started_at": datetime.now().isoformat(),
                "deadline": deadline.isoformat()
            })

            # 5. Challenge içeriğini kanala gönder
            await self._post_challenge_content(challenge_channel_id, enhanced_project, challenge)

            # 6. Deadline sonrası kapatma görevi planla
            self.cron.add_once_job(
                func=self._close_challenge,
                delay_minutes=challenge["deadline_hours"] * 60,
                job_id=f"close_challenge_{challenge_id}",
                args=[challenge_id, challenge_channel_id]
            )

            logger.info(f"[+] Challenge başlatıldı | ID: {challenge_id} | Kanal: {challenge_channel_id}")

        except Exception as e:
            logger.error(f"[X] ChallengeHubService._start_challenge hatası: {e}", exc_info=True)

    async def _post_challenge_content(
        self,
        channel_id: str,
        project: Dict,
        challenge: Dict
    ):
        """
        Challenge içeriğini kanala gönderir.
        """
        try:
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🎯 Challenge Başladı: {project.get('name', 'Proje')}",
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

            # Görevler
            tasks = project.get("tasks", [])
            if isinstance(tasks, str):
                try:
                    tasks = json.loads(tasks)
                except:
                    tasks = []

            if tasks:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*📋 Görevler:*"
                    }
                })

                for i, task in enumerate(tasks[:10], 1):  # İlk 10 görev
                    task_title = task.get("title", task.get("name", f"Görev {i}"))
                    task_desc = task.get("description", "")
                    task_hours = task.get("estimated_hours", 8)
                    
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{i}. {task_title}*\n"
                                f"{task_desc}\n"
                                f"⏱️ Tahmini Süre: {task_hours} saat"
                            )
                        }
                    })

            # LLM özellikleri
            llm_features = project.get("llm_enhanced_features", [])
            if llm_features:
                blocks.append({"type": "divider"})
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*✨ LLM Özelleştirmeleri:*"
                    }
                })

                for feature in llm_features:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{feature.get('name', 'Özellik')}*\n"
                                f"{feature.get('description', '')}"
                            )
                        }
                    })

            # Süre bilgisi
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"⏰ *Süre:* {challenge['deadline_hours']} saat\n📅 *Bitiş:* {challenge.get('deadline', 'N/A')}"
                }
            })

            self.chat.post_message(
                channel=channel_id,
                text=f"🎯 Challenge Başladı: {project.get('name', 'Proje')}",
                blocks=blocks
            )

        except Exception as e:
            logger.error(f"[X] Challenge içeriği gönderme hatası: {e}", exc_info=True)

    async def _close_challenge(self, challenge_id: str, channel_id: str):
        """
        Challenge'ı kapatır (deadline sonrası).
        """
        try:
            # Mesajları analiz et, özet gönder, kanalı arşivle
            # (Kahve/yardım kanalları gibi)
            self.conv.archive_channel(channel_id)
            self.hub_repo.update(challenge_id, {
                "status": "completed",
                "completed_at": datetime.now().isoformat()
            })
            logger.info(f"[+] Challenge kapatıldı | ID: {challenge_id}")
        except Exception as e:
            logger.error(f"[X] Challenge kapatma hatası: {e}", exc_info=True)

    def _get_hub_channel(self) -> Optional[str]:
        """#challenge-hub kanalını bulur."""
        from src.core.settings import get_settings
        settings = get_settings()
        # Settings'den al veya varsayılan olarak None döndür
        # Kullanıcı #challenge-hub kanalını manuel oluşturmalı
        return None

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
