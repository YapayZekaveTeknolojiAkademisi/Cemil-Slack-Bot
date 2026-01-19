"""
Challenge değerlendirme servisi.
"""

import uuid
import re
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from src.core.logger import logger
from src.commands import ChatManager, ConversationManager
from src.repositories import (
    ChallengeEvaluationRepository,
    ChallengeEvaluatorRepository,
    ChallengeHubRepository
)
from src.clients import CronClient


class ChallengeEvaluationService:
    """Challenge değerlendirme yönetim servisi."""

    def __init__(
        self,
        chat_manager: ChatManager,
        conv_manager: ConversationManager,
        evaluation_repo: ChallengeEvaluationRepository,
        evaluator_repo: ChallengeEvaluatorRepository,
        hub_repo: ChallengeHubRepository,
        cron_client: CronClient
    ):
        self.chat = chat_manager
        self.conv = conv_manager
        self.evaluation_repo = evaluation_repo
        self.evaluator_repo = evaluator_repo
        self.hub_repo = hub_repo
        self.cron = cron_client

    async def start_evaluation(
        self,
        challenge_id: str,
        trigger_channel_id: str
    ) -> Dict[str, Any]:
        """
        Challenge için değerlendirme başlatır.
        Challenge kanalına 'Projeyi Değerlendir' butonu gönderir.
        """
        try:
            # Challenge kontrolü
            challenge = self.hub_repo.get(challenge_id)
            if not challenge:
                return {
                    "success": False,
                    "message": "❌ Challenge bulunamadı."
                }

            # Zaten değerlendirme başlatılmış mı?
            existing = self.evaluation_repo.get_by_challenge(challenge_id)
            if existing:
                return {
                    "success": False,
                    "message": "⚠️ Bu challenge için değerlendirme zaten başlatılmış."
                }

            # Değerlendirme kaydı oluştur
            evaluation_id = str(uuid.uuid4())
            deadline = datetime.now() + timedelta(hours=48)
            
            evaluation_data = {
                "id": evaluation_id,
                "challenge_hub_id": challenge_id,
                "status": "pending",
                "deadline_at": deadline.isoformat()
            }
            self.evaluation_repo.create(evaluation_data)

            # Mesajın gönderileceği kanal:
            # Öncelik: hub_channel (challenge ilanının olduğu ortak kanal),
            # yoksa tetikleyen kanal (trigger_channel_id)
            target_channel = challenge.get("hub_channel_id") or trigger_channel_id

            # Challenge kanalına mesaj gönder
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🎯 Challenge Tamamlandı!",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "Projeyi değerlendirmek için butona tıklayın.\n"
                            "Max 3 değerlendirici alınacak.\n\n"
                            "💡 *Değerlendirme Süreci:*\n"
                            "• Değerlendirme kanalı 48 saat açık kalacak\n"
                            "• Her değerlendirici `/challenge set True` veya `/challenge set False` yazacak\n"
                            "• Başarılı sayılması için True > False ve public GitHub repo gerekli"
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
                                "text": "📊 Projeyi Değerlendir",
                                "emoji": True
                            },
                            "style": "primary",
                            "action_id": "evaluate_challenge_button",
                            "value": evaluation_id
                        }
                    ]
                }
            ]

            self.chat.post_message(
                channel=target_channel,
                text="🎯 Challenge Tamamlandı! Projeyi değerlendirmek için butona tıklayın.",
                blocks=blocks
            )

            logger.info(f"[+] Değerlendirme başlatıldı | Challenge: {challenge_id} | Evaluation: {evaluation_id}")

            return {
                "success": True,
                "evaluation_id": evaluation_id,
                "message": "✅ Değerlendirme başlatıldı!"
            }

        except Exception as e:
            logger.error(f"[X] Değerlendirme başlatma hatası: {e}", exc_info=True)
            return {
                "success": False,
                "message": "❌ Değerlendirme başlatılırken bir hata oluştu."
            }

    async def join_evaluation(
        self,
        evaluation_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        Kullanıcıyı değerlendirme kanalına ekler.
        Max 3 kişi kontrolü yapar.
        """
        try:
            evaluation = self.evaluation_repo.get(evaluation_id)
            if not evaluation:
                return {
                    "success": False,
                    "message": "❌ Değerlendirme bulunamadı."
                }

            # Max 3 kişi kontrolü
            evaluator_count = self.evaluator_repo.count_evaluators(evaluation_id)
            if evaluator_count >= 3:
                return {
                    "success": False,
                    "message": "❌ Değerlendirme kanalı dolu (max 3 kişi)."
                }

            # Zaten eklenmiş mi?
            existing = self.evaluator_repo.get_by_evaluation_and_user(evaluation_id, user_id)
            if existing:
                return {
                    "success": False,
                    "message": "⚠️ Zaten değerlendirme kanalındasınız."
                }

            # İlk kişi ise kanal oluştur
            eval_channel_id = evaluation.get("evaluation_channel_id")
            if evaluator_count == 0:
                challenge = self.hub_repo.get(evaluation["challenge_hub_id"])
                if not challenge:
                    return {
                        "success": False,
                        "message": "❌ Challenge bulunamadı."
                    }

                # Kanal oluştur
                channel_suffix = str(uuid.uuid4())[:8]
                channel_name = f"challenge-evaluation-{channel_suffix}"
                
                try:
                    eval_channel = self.conv.create_channel(
                        name=channel_name,
                        is_private=True
                    )
                    eval_channel_id = eval_channel["id"]
                    
                    # Değerlendirme kaydını güncelle
                    self.evaluation_repo.update(evaluation_id, {
                        "evaluation_channel_id": eval_channel_id,
                        "status": "evaluating"
                    })
                    
                    # Açılış mesajı gönder
                    welcome_blocks = [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": "📊 Challenge Değerlendirme",
                                "emoji": True
                            }
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    "Bu kanal 48 saat açık kalacak.\n\n"
                                    "*Komutlar:*\n"
                                    "• `/challenge set True` - Proje başarılı\n"
                                    "• `/challenge set False` - Proje başarısız\n"
                                    "• `/challenge set github <link>` - GitHub repo linki\n\n"
                                    "💡 *Not:* Başarılı sayılması için True > False ve public GitHub repo gerekli."
                                )
                            }
                        }
                    ]
                    
                    self.chat.post_message(
                        channel=eval_channel_id,
                        text="📊 Challenge Değerlendirme",
                        blocks=welcome_blocks
                    )

                    # 48 saat sonra otomatik kapatma görevi planla
                    self.cron.add_once_job(
                        func=self.finalize_evaluation,
                        delay_minutes=48 * 60,
                        job_id=f"finalize_evaluation_{evaluation_id}",
                        args=[evaluation_id]
                    )

                    logger.info(f"[+] Değerlendirme kanalı oluşturuldu: {eval_channel_id}")
                except Exception as e:
                    logger.error(f"[X] Değerlendirme kanalı oluşturulamadı: {e}", exc_info=True)
                    return {
                        "success": False,
                        "message": "❌ Değerlendirme kanalı oluşturulamadı."
                    }

            # Kullanıcıyı kanala ekle
            if not eval_channel_id:
                return {
                    "success": False,
                    "message": "❌ Değerlendirme kanalı bulunamadı."
                }

            try:
                self.conv.invite_users(eval_channel_id, [user_id])
            except Exception as e:
                logger.warning(f"[!] Kullanıcı kanala davet edilemedi: {e}")

            # Değerlendirici kaydı oluştur
            evaluator_id = str(uuid.uuid4())
            self.evaluator_repo.create({
                "id": evaluator_id,
                "evaluation_id": evaluation_id,
                "user_id": user_id
            })

            logger.info(f"[+] Değerlendirici eklendi: {user_id} | Evaluation: {evaluation_id}")

            return {
                "success": True,
                "message": f"✅ Değerlendirme kanalına eklendiniz! <#{eval_channel_id}>"
            }

        except Exception as e:
            logger.error(f"[X] Değerlendirme katılma hatası: {e}", exc_info=True)
            return {
                "success": False,
                "message": "❌ Değerlendirme kanalına eklenirken bir hata oluştu."
            }

    async def submit_vote(
        self,
        evaluation_id: str,
        user_id: str,
        vote: str
    ) -> Dict[str, Any]:
        """Kullanıcının oyunu kaydeder."""
        try:
            evaluation = self.evaluation_repo.get(evaluation_id)
            if not evaluation:
                return {
                    "success": False,
                    "message": "❌ Değerlendirme bulunamadı."
                }

            # Değerlendirici kontrolü
            evaluator = self.evaluator_repo.get_by_evaluation_and_user(evaluation_id, user_id)
            if not evaluator:
                return {
                    "success": False,
                    "message": "❌ Bu değerlendirmenin değerlendiricisi değilsiniz."
                }

            # Zaten oy vermiş mi?
            if evaluator.get("vote"):
                return {
                    "success": False,
                    "message": "⚠️ Zaten oy verdiniz. Oyunuzu değiştiremezsiniz."
                }

            # Oyu kaydet
            self.evaluator_repo.update(evaluator["id"], {
                "vote": vote.lower(),
                "voted_at": datetime.now().isoformat()
            })

            # Oyları güncelle
            votes = self.evaluator_repo.get_votes(evaluation_id)
            self.evaluation_repo.update_votes(
                evaluation_id,
                votes["true"],
                votes["false"]
            )

            logger.info(f"[+] Oy kaydedildi: {user_id} | Vote: {vote} | Evaluation: {evaluation_id}")

            return {
                "success": True,
                "message": f"✅ Oyunuz kaydedildi: *{vote}*"
            }

        except Exception as e:
            logger.error(f"[X] Oy kaydetme hatası: {e}", exc_info=True)
            return {
                "success": False,
                "message": "❌ Oy kaydedilirken bir hata oluştu."
            }

    async def submit_github_link(
        self,
        evaluation_id: str,
        github_url: str
    ) -> Dict[str, Any]:
        """GitHub repo linkini kaydeder ve public kontrolü yapar."""
        try:
            evaluation = self.evaluation_repo.get(evaluation_id)
            if not evaluation:
                return {
                    "success": False,
                    "message": "❌ Değerlendirme bulunamadı."
                }

            # GitHub URL formatını kontrol et
            if not self._is_valid_github_url(github_url):
                return {
                    "success": False,
                    "message": "❌ Geçersiz GitHub URL formatı. Örnek: https://github.com/user/repo"
                }

            # Repo public mi kontrol et
            is_public = await self.check_github_repo_public(github_url)

            # Linki kaydet
            self.evaluation_repo.update(evaluation_id, {
                "github_repo_url": github_url,
                "github_repo_public": 1 if is_public else 0
            })

            if is_public:
                return {
                    "success": True,
                    "message": f"✅ GitHub repo linki kaydedildi ve public olarak doğrulandı: {github_url}"
                }
            else:
                return {
                    "success": True,
                    "message": f"⚠️ GitHub repo linki kaydedildi ancak repo private görünüyor: {github_url}\n\n💡 Başarılı sayılması için repo public olmalı."
                }

        except Exception as e:
            logger.error(f"[X] GitHub link kaydetme hatası: {e}", exc_info=True)
            return {
                "success": False,
                "message": "❌ GitHub linki kaydedilirken bir hata oluştu."
            }

    async def check_github_repo_public(self, github_url: str) -> bool:
        """GitHub repo'nun public olup olmadığını kontrol eder."""
        try:
            # GitHub URL'ini parse et
            # https://github.com/user/repo -> https://api.github.com/repos/user/repo
            match = re.match(r'https?://github\.com/([^/]+)/([^/]+)', github_url)
            if not match:
                return False

            user, repo = match.groups()
            api_url = f"https://api.github.com/repos/{user}/{repo}"

            # API'ye istek at
            response = requests.get(api_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return not data.get("private", True)
            elif response.status_code == 404:
                # Repo bulunamadı veya private
                return False
            else:
                logger.warning(f"[!] GitHub API hatası: {response.status_code}")
                return False

        except Exception as e:
            logger.warning(f"[!] GitHub repo kontrolü hatası: {e}")
            return False

    def _is_valid_github_url(self, url: str) -> bool:
        """GitHub URL formatını kontrol eder."""
        pattern = r'^https?://github\.com/[^/]+/[^/]+/?$'
        return bool(re.match(pattern, url))

    async def finalize_evaluation(self, evaluation_id: str):
        """48 saat sonunda değerlendirmeyi finalize eder."""
        try:
            evaluation = self.evaluation_repo.get(evaluation_id)
            if not evaluation:
                logger.error(f"[X] Finalize: Değerlendirme bulunamadı: {evaluation_id}")
                return

            if evaluation.get("status") != "evaluating":
                logger.warning(f"[!] Finalize: Değerlendirme zaten tamamlanmış: {evaluation_id}")
                return

            # Oyları al
            votes = self.evaluator_repo.get_votes(evaluation_id)
            true_votes = votes["true"]
            false_votes = votes["false"]

            # Sonucu hesapla
            github_public = evaluation.get("github_repo_public", 0) == 1
            github_url = evaluation.get("github_repo_url")

            if true_votes > false_votes and github_public and github_url:
                final_result = "success"
                result_message = "🎉 *Challenge Başarılı!*"
            else:
                final_result = "failed"
                reasons = []
                if true_votes <= false_votes:
                    reasons.append(f"True oyları ({true_votes}) False oylarından ({false_votes}) fazla değil")
                if not github_url:
                    reasons.append("GitHub repo linki eklenmemiş")
                elif not github_public:
                    reasons.append("GitHub repo public değil")
                result_message = f"❌ *Challenge Başarısız*\n\n*Nedenler:*\n" + "\n".join(f"• {r}" for r in reasons)

            # Değerlendirmeyi güncelle
            self.evaluation_repo.update(evaluation_id, {
                "status": "completed",
                "final_result": final_result,
                "completed_at": datetime.now().isoformat()
            })

            # Challenge'ı güncelle
            challenge_id = evaluation["challenge_hub_id"]
            challenge = self.hub_repo.get(challenge_id)
            if challenge:
                challenge_channel_id = challenge.get("challenge_channel_id")
                if challenge_channel_id:
                    # Sonuç mesajı gönder (kanal arşivlenmiş olabilir, hata kontrolü yap)
                    try:
                        result_blocks = [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": result_message
                                }
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"📊 Oylar: True={true_votes}, False={false_votes} | GitHub: {'✅ Public' if github_public else '❌ Private/Missing'}"
                                    }
                                ]
                            }
                        ]
                        self.chat.post_message(
                            channel=challenge_channel_id,
                            text=result_message,
                            blocks=result_blocks
                        )
                    except Exception as e:
                        logger.warning(f"[!] Challenge kanalına sonuç mesajı gönderilemedi (kanal arşivlenmiş olabilir): {e}")

            # Değerlendirme kanalını kapat
            eval_channel_id = evaluation.get("evaluation_channel_id")
            if eval_channel_id:
                try:
                    self.conv.archive_channel(eval_channel_id)
                    logger.info(f"[+] Değerlendirme kanalı arşivlendi: {eval_channel_id}")
                except Exception as e:
                    logger.warning(f"[!] Değerlendirme kanalı arşivlenemedi: {e}")

            logger.info(f"[+] Değerlendirme finalize edildi: {evaluation_id} | Sonuç: {final_result}")

        except Exception as e:
            logger.error(f"[X] Değerlendirme finalize hatası: {e}", exc_info=True)
