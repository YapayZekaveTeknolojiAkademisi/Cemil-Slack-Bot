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
    ChallengeHubRepository,
    ChallengeParticipantRepository,
    UserChallengeStatsRepository
)
from src.clients import CronClient
from src.core.settings import get_settings


class ChallengeEvaluationService:
    """Challenge değerlendirme yönetim servisi."""

    def __init__(
        self,
        chat_manager: ChatManager,
        conv_manager: ConversationManager,
        evaluation_repo: ChallengeEvaluationRepository,
        evaluator_repo: ChallengeEvaluatorRepository,
        hub_repo: ChallengeHubRepository,
        participant_repo: ChallengeParticipantRepository,
        stats_repo: UserChallengeStatsRepository,
        cron_client: CronClient
    ):
        self.chat = chat_manager
        self.conv = conv_manager
        self.evaluation_repo = evaluation_repo
        self.evaluator_repo = evaluator_repo
        self.hub_repo = hub_repo
        self.participant_repo = participant_repo
        self.stats_repo = stats_repo
        self.cron = cron_client

    async def update_challenge_canvas(self, challenge_id: str = None) -> None:
        """
        Duyuru kanalındaki challenge özet/canvas mesajını günceller veya yoksa oluşturur.
        Tüm aktif challenge'ları yatay tablo formatında gösterir.
        Her challenge bir satır olarak eklenir.
        
        Args:
            challenge_id: Belirli bir challenge için güncelleme (opsiyonel, None ise tüm aktif challenge'lar)
        """
        try:
            # Tüm aktif challenge'ları al
            all_active_challenges = self.hub_repo.get_all_active()
            
            if not all_active_challenges:
                logger.debug("[i] Aktif challenge yok, canvas güncellenmeyecek")
                return
            
            # İlk challenge'dan hub_channel_id'yi al (tüm challenge'lar aynı kanalda olmalı)
            first_challenge = all_active_challenges[0]
            hub_channel_id = first_challenge.get("hub_channel_id")
            
            if not hub_channel_id:
                logger.warning(
                    f"[!] Canvas güncelleme: hub_channel_id YOK | "
                    f"Toplam aktif challenge: {len(all_active_challenges)}"
                )
                return
            
            logger.info(
                f"[>] Canvas güncelleme başlıyor | "
                f"Toplam aktif challenge: {len(all_active_challenges)} | "
                f"Kanal: {hub_channel_id}"
            )

            # Tüm aktif challenge'lar için veri topla
            table_rows = []
            
            for ch in all_active_challenges:
                ch_id = ch.get("id")
                evaluation = self.evaluation_repo.get_by_challenge(ch_id)
                
                # Challenge bilgileri
                theme = ch.get("theme", "N/A")
                project_name = ch.get("project_name") or "Belirlenmedi"
                status = ch.get("status", "unknown")
                
                # Durum etiketi
                if status == "recruiting":
                    status_label = "📋 Toplanıyor"
                elif status == "active":
                    status_label = "🚀 Geliştirme"
                elif status == "evaluating":
                    status_label = "⚖️ Değerlendirme"
                elif status == "completed":
                    final_result = evaluation.get("final_result") if evaluation else None
                    status_label = "✅ Başarılı" if final_result == "success" else "❌ Başarısız"
                else:
                    status_label = "❓ Bilinmiyor"
                
                # Bitiş tarihi
                deadline = ch.get("deadline")
                deadline_text = (
                    datetime.fromisoformat(deadline).strftime("%d.%m %H:%M")
                    if deadline else "Belirlenmedi"
                )
                
                # Katılımcı sayısı
                participants = self.participant_repo.get_team_members(ch_id)
                participant_count = len(participants)
                team_size = ch.get("team_size", 0)
                team_info = f"{participant_count}/{team_size}"
                
                # GitHub durumu
                github_info = "❌ Yok"
                if evaluation:
                    github_url = evaluation.get("github_repo_url")
                    if github_url:
                        github_public = evaluation.get("github_repo_public", 0) == 1
                        github_info = "✅ Public" if github_public else "⚠️ Private"
                
                # Oylar
                votes_info = "-"
                if evaluation:
                    try:
                        votes = self.evaluator_repo.get_votes(evaluation["id"])
                        true_votes = votes.get("true", 0)
                        false_votes = votes.get("false", 0)
                        if true_votes > 0 or false_votes > 0:
                            votes_info = f"✅{true_votes} ❌{false_votes}"
                    except:
                        pass
                
                # Tablo satırı ekle
                table_rows.append({
                    "theme": theme[:20],
                    "project": project_name[:25],
                    "status": status_label,
                    "deadline": deadline_text,
                    "team": team_info,
                    "github": github_info,
                    "votes": votes_info
                })
            
            # Yatay tablo formatında canvas mesajı oluştur
            # Slack'te monospace font için code block kullan
            table_lines = [
                "```",
                f"{'Tema':<20} | {'Proje':<25} | {'Durum':<15} | {'Bitiş':<12} | {'Takım':<8} | {'GitHub':<12} | {'Oylar':<10}",
                "-" * 120
            ]
            
            for row in table_rows:
                table_lines.append(
                    f"{row['theme']:<20} | {row['project']:<25} | {row['status']:<15} | "
                    f"{row['deadline']:<12} | {row['team']:<8} | {row['github']:<12} | {row['votes']:<10}"
                )
            
            table_lines.append("```")
            table_text = "\n".join(table_lines)
            
            # Canvas mesajı blocks
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "📊 Aktif Challenge'lar",
                        "emoji": True
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": table_text
                    }
                }
            ]
            
            # İlk challenge'dan summary_ts al (tüm challenge'lar aynı canvas mesajını kullanır)
            summary_ts = first_challenge.get("summary_message_ts")

            # Canvas mesajı için text fallback
            canvas_text = f"📊 Aktif Challenge'lar ({len(all_active_challenges)} adet)\n{table_text}"
            
            # Mevcut mesajı güncelle veya yeni mesaj oluştur
            if summary_ts:
                try:
                    self.chat.update_message(
                        channel=hub_channel_id,
                        ts=summary_ts,
                        text=canvas_text,
                        blocks=blocks,
                    )
                    logger.info(
                        f"[+] Canvas tablo GÜNCELLENDİ | "
                        f"Kanal: {hub_channel_id} | "
                        f"TS: {summary_ts} | "
                        f"Toplam challenge: {len(all_active_challenges)}"
                    )
                    return
                except Exception as e:
                    logger.warning(f"[!] Canvas mesajı güncellenemedi, yeniden oluşturulacak: {e}")

            # Yeni mesaj oluştur
            try:
                logger.debug(
                    f"[DEBUG] Canvas tablo mesajı gönderiliyor | "
                    f"Kanal: {hub_channel_id} | "
                    f"Toplam challenge: {len(all_active_challenges)} | "
                    f"Blocks sayısı: {len(blocks)}"
                )
                
                resp = self.chat.post_message(
                    channel=hub_channel_id,
                    text=canvas_text,
                    blocks=blocks,
                )
                
                # Response'u detaylı logla
                logger.debug(f"[DEBUG] Canvas post_message response: {resp}")
                
                ts = resp.get("ts") or (resp.get("message") or {}).get("ts")
                message_data = resp.get("message", {})
                
                if ts:
                    # Tüm aktif challenge'lara aynı summary_ts'yi kaydet
                    for ch in all_active_challenges:
                        self.hub_repo.update(
                            ch.get("id"),
                            {
                                "summary_message_ts": ts,
                                "summary_message_channel_id": hub_channel_id,
                            },
                        )
                    logger.info(
                        f"[+] YENİ canvas tablo mesajı OLUŞTURULDU | "
                        f"Kanal: {hub_channel_id} | "
                        f"TS: {ts} | "
                        f"Toplam challenge: {len(all_active_challenges)} | "
                        f"Message Type: {message_data.get('type', 'N/A')}"
                    )
                    
                    # Mesajın Slack'te gerçekten var olup olmadığını kontrol et
                    try:
                        messages = self.conv.get_history(channel_id=hub_channel_id, limit=5)
                        found = any(msg.get("ts") == ts for msg in messages)
                        if found:
                            logger.info(f"[✓] Canvas mesajı Slack kanalında DOĞRULANDI | TS: {ts}")
                        else:
                            logger.warning(
                                f"[!] Canvas mesajı gönderildi ama kanal geçmişinde BULUNAMADI! | "
                                f"TS: {ts} | Kanal: {hub_channel_id} | "
                                f"Son 5 mesaj kontrol edildi"
                            )
                    except Exception as e:
                        logger.warning(f"[!] Canvas mesajı doğrulama hatası: {e}")
                else:
                    logger.error(
                        f"[X] Canvas mesajı gönderildi ama TS alınamadı! | "
                        f"Kanal: {hub_channel_id} | "
                        f"Toplam challenge: {len(all_active_challenges)} | "
                        f"Response OK: {resp.get('ok', False)} | "
                        f"Response: {str(resp)[:300]}"
                    )
            except Exception as e:
                logger.warning(f"[!] Canvas mesajı oluşturulamadı: {e}")
        except Exception as e:
            logger.error(f"[X] Canvas güncelleme hatası: {e}", exc_info=True)

    async def start_evaluation(
        self,
        challenge_id: str,
        trigger_channel_id: str
    ) -> Dict[str, Any]:
        """
        Challenge için değerlendirme başlatır.
        Değerlendirme kanalını otomatik oluşturur ve tüm katılımcıları ekler.
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

            # 1. Değerlendirme kanalını HEMEN oluştur
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
                
                logger.info(f"[+] Değerlendirme kanalı oluşturuldu: {eval_channel_id} | Challenge: {challenge_id}")
            except Exception as e:
                logger.error(f"[X] Değerlendirme kanalı oluşturulamadı: {e}", exc_info=True)
                return {
                    "success": False,
                    "message": "❌ Değerlendirme kanalı oluşturulamadı."
                }

            # 2. Tüm katılımcıları kanala ekle (creator + participants + admin)
            settings = get_settings()
            ADMIN_USER_ID = settings.admin_slack_id
            creator_id = challenge.get("creator_id")
            participants = self.participant_repo.list(filters={"challenge_hub_id": challenge_id})
            participant_ids = [p["user_id"] for p in participants]
            
            # Tüm kullanıcıları birleştir (tekrarları önle)
            all_user_ids = set()
            if creator_id:
                all_user_ids.add(creator_id)
            for pid in participant_ids:
                all_user_ids.add(pid)
            if ADMIN_USER_ID:
                all_user_ids.add(ADMIN_USER_ID)
            
            # Kullanıcıları kanala davet et
            try:
                self.conv.invite_users(eval_channel_id, list(all_user_ids))
                logger.info(f"[+] {len(all_user_ids)} kullanıcı değerlendirme kanalına eklendi | Evaluation: {evaluation_id}")
            except Exception as e:
                logger.warning(f"[!] Kullanıcılar kanala davet edilirken hata: {e}")

            # 3. 48 saat sonra otomatik kapatma görevi planla
            self.cron.add_once_job(
                func=self.finalize_evaluation,
                delay_minutes=48 * 60,
                job_id=f"finalize_evaluation_{evaluation_id}",
                args=[evaluation_id]
            )
            logger.info(f"[+] 48 saatlik değerlendirme timer'ı başlatıldı | Evaluation: {evaluation_id}")

            # 4. Kanal açılış mesajını gönder (EKİP İÇİN)
            welcome_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "👋 *Değerlendirme Başladı!*\n\n"
                            "3 kişilik jüri ekibi bekleniyor. Jüri gelince değerlendirme başlayacak.\n\n"
                            "💡 GitHub linki ekleyin: `/challenge set github <link>`"
                        )
                    }
                }
            ]
            
            try:
                self.chat.post_message(
                    channel=eval_channel_id,
                    text="👋 Değerlendirme Başladı!",
                    blocks=welcome_blocks
                )
                
                # 5. Challenge kanalına yönlendirme mesajı gönder
                challenge_channel_id = challenge.get("challenge_channel_id")
                if challenge_channel_id:
                    try:
                        self.chat.post_message(
                            channel=challenge_channel_id,
                            text="🚀 Challenge tamamlandı! Değerlendirme süreci başladı.",
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": (
                                            "🚀 *Challenge Tamamlandı!*\n\n"
                                            f"Değerlendirme süreci başladı. Lütfen <#{eval_channel_id}> kanalında devam edin.\n\n"
                                            "💡 *Not:* Tüm ekip üyeleri otomatik olarak değerlendirme kanalına eklendi."
                                        )
                                    }
                                }
                            ]
                        )
                        logger.info(f"[+] Challenge kanalına yönlendirme mesajı gönderildi: {challenge_channel_id}")
                    except Exception as e:
                        logger.warning(f"[!] Challenge kanalına yönlendirme mesajı gönderilemedi: {e}")
            except Exception as e:
                logger.warning(f"[!] Değerlendirme açılış mesajı gönderilemedi: {e}")

            # 5. Topluluk kanalına JÜRİ ÇAĞRISI gönder
            target_channel = challenge.get("hub_channel_id") or trigger_channel_id
            
            # Proje bilgilerini al
            theme = challenge.get("theme", "Proje")
            project_name = challenge.get("project_name") or "Proje adı henüz belirlenmedi"
            project_description = challenge.get("project_description") or "Henüz açıklama bulunmuyor."
            
            # Katılımcıları al
            participants = self.participant_repo.get_team_members(challenge_id)
            participant_ids = [p["user_id"] for p in participants]
            creator_id = challenge.get("creator_id")
            if creator_id and creator_id not in participant_ids:
                participant_ids.insert(0, creator_id)
            
            participants_text = (
                ", ".join(f"<@{uid}>" for uid in participant_ids[:5])  # İlk 5 kişiyi göster
                if participant_ids else "Henüz katılımcı yok."
            )
            if len(participant_ids) > 5:
                participants_text += f" ve {len(participant_ids) - 5} kişi daha"
            
            info_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"📣 *Jüri Aranıyor*\n"
                            f"🎯 *{theme}* – *{project_name}*\n"
                            f"👥 Takım: {participants_text}\n\n"
                            f"💡 {project_description[:150]}{'...' if len(project_description) > 150 else ''}"
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
                                "text": "🙋 Jüri Ol (0/3)",
                                "emoji": True
                            },
                            "style": "primary",
                            "action_id": "challenge_join_jury_toggle",
                            "value": evaluation_id
                        }
                    ]
                }
            ]

            self.chat.post_message(
                channel=target_channel,
                text=f"📣 Jüri Aranıyor: {challenge.get('theme')}",
                blocks=info_blocks
            )

            # Duyuru kanalındaki challenge canvas/özet mesajını güncelle
            try:
                await self.update_challenge_canvas(challenge_id)
            except Exception as e:
                logger.warning(f"[!] Değerlendirme başlangıcında canvas güncellenemedi: {e}")

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

    async def toggle_juror(
        self,
        evaluation_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        Kullanıcıyı jüri havuzuna ekler veya çıkarır (Toggle).
        3 kişi dolduğunda toplu olarak kanala davet eder.
        
        Jury Status Durumları:
        - 'recruiting': 0-2 kişi, toggle serbestçe yapılabilir
        - 'finalizing': 3. kişi eklendi, davet işlemleri yapılıyor (toggle KİLİTLİ)
        - 'locked': Davet tamamlandı, jüri ekibi artık değiştirilemez (toggle KİLİTLİ)
        """
        try:
            evaluation = self.evaluation_repo.get(evaluation_id)
            if not evaluation:
                return {"success": False, "message": "❌ Değerlendirme bulunamadı."}

            challenge = self.hub_repo.get(evaluation["challenge_hub_id"])
            if not challenge:
                return {"success": False, "message": "❌ Challenge bulunamadı."}

            # 1. STATUS KONTROLÜ - Jüri finalize edilmişse/ediliyorsa toggle yapılamaz
            jury_status = evaluation.get("jury_status", "recruiting")
            
            if jury_status in ["finalizing", "locked"]:
                return {
                    "success": False,
                    "message": "⚠️ Jüri ekibi tamamlandı veya işlem yapılıyor. Artık değişiklik yapılamaz.",
                    "action": "locked",
                    "status": jury_status
                }

            # 2. Proje sahibi/üyesi/admin kontrolü - bunlar jüri olamaz
            settings = get_settings()
            ADMIN_USER_ID = settings.admin_slack_id
            creator_id = challenge.get("creator_id")
            participants = self.participant_repo.list(filters={"challenge_hub_id": challenge["id"]})
            participant_ids = [p["user_id"] for p in participants]

            if user_id == ADMIN_USER_ID or user_id == creator_id or user_id in participant_ids:
                return {
                    "success": False,
                    "message": "⚠️ Proje ekibi veya admin jüri olamaz.",
                    "action": "none"
                }

            # 3. Zaten jüri mi? (Toggle Mantığı)
            existing_juror = self.evaluator_repo.get_by_evaluation_and_user(evaluation_id, user_id)
            
            if existing_juror:
                # VARSA -> ÇIKAR (LEAVE)
                # Status 'recruiting' olduğu için çıkabilir
                self.evaluator_repo.delete(existing_juror["id"])
                logger.info(f"[-] Jüri havuzundan çıktı: {user_id} | Evaluation: {evaluation_id}")
                
                # Güncel sayıyı al
                count = self.evaluator_repo.count_evaluators(evaluation_id)
                
                # DM Gönder
                try:
                    dm_channel = self.conv.open_conversation([user_id])
                    if dm_channel:
                         self.chat.post_message(
                            channel=dm_channel["channel"]["id"],
                            text=f"ℹ️ `{challenge.get('theme')}` projesi jüri adaylığından çekildiniz."
                        )
                except: pass
                
                return {
                    "success": True,
                    "message": "❌ Jüri adaylığından çekildiniz.",
                    "action": "left",
                    "count": count,
                    "max": 3
                }
            
            else:
                # YOKSA -> EKLE (JOIN)
                # Önce kontenjan dolu mu kontrol et
                current_count = self.evaluator_repo.count_evaluators(evaluation_id)
                if current_count >= 3:
                    return {
                        "success": False,
                        "message": "⚠️ Jüri kontenjanı dolu (3/3).",
                        "action": "full"
                    }

                # Kullanıcıyı users tablosuna ekle (foreign key için gerekli)
                try:
                    from src.clients import DatabaseClient
                    db_client = DatabaseClient(db_path=settings.database_path)
                    
                    with db_client.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT id FROM users WHERE slack_id = ?", (user_id,))
                        user_exists = cursor.fetchone()
                        
                        if not user_exists:
                            # Kullanıcı yoksa otomatik ekle
                            user_uuid = str(uuid.uuid4())
                            cursor.execute("""
                                INSERT INTO users (id, slack_id, full_name, created_at, updated_at)
                                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """, (user_uuid, user_id, f"User {user_id}"))
                            conn.commit()
                            logger.info(f"[+] Jüri için kullanıcı otomatik eklendi: {user_id}")
                except Exception as e:
                    logger.warning(f"[!] Kullanıcı kontrolü/ekleme hatası (jüri): {e}")

                # Havuza ekle
                juror_id = str(uuid.uuid4())
                self.evaluator_repo.create({
                    "id": juror_id,
                    "evaluation_id": evaluation_id,
                    "user_id": user_id
                })
                current_count += 1
                logger.info(f"[+] Jüri havuzuna eklendi: {user_id} | Evaluation: {evaluation_id}")
                
                # DM Gönder
                try:
                    dm_channel = self.conv.open_conversation([user_id])
                    if dm_channel:
                         self.chat.post_message(
                            channel=dm_channel["channel"]["id"],
                            text=(
                                f"🎉 `{challenge.get('theme')}` projesi için jüri adaylığınız alındı!\n"
                                f"Şu an *{current_count}/3* kişiyiz. 3 kişi tamamlandığında otomatik olarak kanala ekleneceksiniz.\n\n"
                                "O zamana kadar bekleyiniz..."
                            )
                        )
                except: pass

                # 4. EĞER 3. KİŞİ İSE -> STATUS KİLİTLE VE TOPLU DAVET BAŞLAT
                if current_count >= 3:
                    # ⚠️ ÖNEMLİ: Önce status'ü "finalizing" yap (LOCK)
                    # Bu sayede başka biri toggle yapamaz
                    self.evaluation_repo.update(evaluation_id, {"jury_status": "finalizing"})
                    logger.info(f"[🔒] Jüri status: 'finalizing' | Evaluation: {evaluation_id}")
                    
                    # 3 Jüriyi Al
                    all_jurors = self.evaluator_repo.list_by_evaluation(evaluation_id)
                    juror_ids = [j["user_id"] for j in all_jurors]
                    
                    # Kanala Davet Et (Batch)
                    eval_channel_id = evaluation.get("evaluation_channel_id")
                    if eval_channel_id:
                        try:
                            self.conv.invite_users(eval_channel_id, juror_ids)
                            logger.info(f"[+] 3 jüri toplu olarak kanala eklendi: {juror_ids}")
                            
                            # Kanal içi karşılama
                            self.chat.post_message(
                                channel=eval_channel_id,
                                text=(
                                    f"✅ *Jüri Ekibi Tamamlandı!*\n"
                                    f"Hoş geldiniz <@{juror_ids[0]}>, <@{juror_ids[1]}>, <@{juror_ids[2]}>!\n"
                                    f"Projeyi inceleyip `/challenge set` ile oyunuzu kullanın."
                                )
                            )
                            
                            # DM ile haber ver
                            for j_id in juror_ids:
                                try:
                                    dm = self.conv.open_conversation([j_id])
                                    if dm:
                                        self.chat.post_message(
                                            channel=dm["channel"]["id"],
                                            text="🚀 Jüri ekibi tamamlandı ve kanala eklendiniz! Görev başına!"
                                        )
                                except: pass
                            
                            # ✅ Davet tamamlandı, status'ü "locked" yap
                            self.evaluation_repo.update(evaluation_id, {"jury_status": "locked"})
                            logger.info(f"[✅] Jüri status: 'locked' | Evaluation: {evaluation_id}")
                                
                        except Exception as e:
                            logger.error(f"[X] Jüri batch davet hatası: {e}")
                            # Hata durumunda status'ü geri al
                            self.evaluation_repo.update(evaluation_id, {"jury_status": "recruiting"})

                return {
                    "success": True,
                    "message": f"✅ Jüri listesine eklendiniz! ({current_count}/3)",
                    "action": "joined",
                    "count": current_count,
                    "max": 3,
                    "is_full": (current_count >= 3),
                    "status": "locked" if current_count >= 3 else "recruiting"
                }

        except Exception as e:
            logger.error(f"[X] toggle_juror hatası: {e}", exc_info=True)
            return {
                "success": False,
                "message": "❌ İşlem sırasında bir hata oluştu."
            }

    async def submit_vote(
        self,
        evaluation_id: str,
        user_id: str,
        vote: str
    ) -> Dict[str, Any]:
        """
        Kullanıcının oyunu kaydeder.
        Sadece harici değerlendiriciler (max 3 kişi) oy verebilir.
        Proje üyeleri ve admin oy veremez (admin sadece onay verebilir).
        """
        try:
            evaluation = self.evaluation_repo.get(evaluation_id)
            if not evaluation:
                return {
                    "success": False,
                    "message": "❌ Değerlendirme bulunamadı."
                }

            # Challenge'ı getir (proje üyesi kontrolü için)
            challenge = self.hub_repo.get(evaluation["challenge_hub_id"])
            if not challenge:
                return {
                    "success": False,
                    "message": "❌ Challenge bulunamadı."
                }

            # Admin oy veremez, sadece onay verebilir
            settings = get_settings()
            ADMIN_USER_ID = settings.admin_slack_id
            if user_id == ADMIN_USER_ID:
                return {
                    "success": False,
                    "message": "❌ Admin olarak oy veremezsiniz. Sadece 'Onayla ve Bitir' / 'Reddet ve Bitir' butonlarını kullanabilirsiniz."
                }

            # Proje ekibi (creator + participants) oy veremez - EN ÜSTTE KONTROL ET
            creator_id = challenge.get("creator_id")
            participants = self.participant_repo.list(filters={"challenge_hub_id": challenge["id"]})
            participant_ids = [p["user_id"] for p in participants]
            
            # Creator kontrolü
            if user_id == creator_id:
                return {
                    "success": False,
                    "message": "❌ Proje sahibi olarak oy veremezsiniz. Sadece harici değerlendiriciler oy kullanabilir."
                }
            
            # Participant kontrolü
            if user_id in participant_ids:
                return {
                    "success": False,
                    "message": "❌ Proje ekibi üyesi olarak oy veremezsiniz. Sadece harici değerlendiriciler oy kullanabilir."
                }

            # Değerlendirici kontrolü (sadece harici değerlendiriciler oy verebilir)
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

            # 3 kişi oy verdiyse kontrol et
            total_votes = votes["true"] + votes["false"]
            if total_votes >= 3:
                logger.info(f"[i] 3 değerlendirici oy verdi | Evaluation: {evaluation_id}")
                
                # GitHub repo var mı ve public mi kontrol et
                github_url = evaluation.get("github_repo_url")
                github_public = evaluation.get("github_repo_public", 0)
                
                eval_channel_id = evaluation.get("evaluation_channel_id")
                
                if github_url and github_public == 1:
                    # Repo var ve public → Admin onayı iste
                    logger.info(f"[+] Tüm oylar alındı ve repo public → Admin onayı bekleniyor | Evaluation: {evaluation_id}")
                    
                    # Kanala admin onay butonu gönder
                    if eval_channel_id:
                        try:
                            self.chat.post_message(
                                channel=eval_channel_id,
                                text="✅ Tüm oylar alındı! Admin onayı bekleniyor...",
                                blocks=[
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": (
                                                f"✅ *Tüm oylar alındı!*\n"
                                                f"📊 Oylar: ✅{votes['true']} ❌{votes['false']} | 🔗 {github_url}\n\n"
                                                "👤 Admin onayı bekleniyor..."
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
                                                    "text": "✅ Onayla ve Bitir",
                                                    "emoji": True
                                                },
                                                "style": "primary",
                                                "action_id": "admin_approve_evaluation",
                                                "value": evaluation_id
                                            },
                                            {
                                                "type": "button",
                                                "text": {
                                                    "type": "plain_text",
                                                    "text": "❌ Reddet ve Bitir",
                                                    "emoji": True
                                                },
                                                "style": "danger",
                                                "action_id": "admin_reject_evaluation",
                                                "value": evaluation_id
                                            }
                                        ]
                                    }
                                ]
                            )
                            logger.info(f"[i] Admin onay butonu gönderildi | Evaluation: {evaluation_id}")
                        except Exception as e:
                            logger.warning(f"[!] Admin onay butonu gönderilemedi: {e}")
                    
                    # Challenge kanalına da bilgilendirme mesajı gönder
                    challenge_id = evaluation.get("challenge_hub_id")
                    if challenge_id:
                        challenge = self.hub_repo.get(challenge_id)
                        challenge_channel_id = challenge.get("challenge_channel_id") if challenge else None
                        
                        if challenge_channel_id:
                            try:
                                self.chat.post_message(
                                    channel=challenge_channel_id,
                                    text="✅ Değerlendirme tamamlandı! Admin onayı bekleniyor...",
                                    blocks=[
                                        {
                                            "type": "section",
                                            "text": {
                                                "type": "mrkdwn",
                                                "text": (
                                                    f"✅ *Değerlendirme Tamamlandı!*\n"
                                                    f"📊 Oylar: ✅{votes['true']} ❌{votes['false']} | 🔗 {github_url}\n\n"
                                                    "👤 Admin onayı bekleniyor..."
                                                )
                                            }
                                        }
                                    ]
                                )
                                logger.info(f"[+] Challenge kanalına admin onay bekleme mesajı gönderildi | Channel: {challenge_channel_id}")
                            except Exception as e:
                                logger.warning(f"[!] Challenge kanalına bilgilendirme gönderilemedi: {e}")
                else:
                    # Repo yok veya private → Bilgilendirme mesajı gönder
                    if eval_channel_id:
                        try:
                            if not github_url:
                                message = (
                                    f"✅ *Tüm oylar alındı!*\n\n"
                                    f"📊 Oylar: ✅{votes['true']} ❌{votes['false']}\n\n"
                                    f"🔗 GitHub linki ekleyin: `/challenge set github <link>`"
                                )
                            else:
                                message = (
                                    f"✅ *Tüm oylar alındı!*\n\n"
                                    f"📊 Oylar: ✅{votes['true']} ❌{votes['false']}\n\n"
                                    f"⚠️ GitHub repo private. Public yapın veya linki güncelleyin: `/challenge set github <link>`"
                                )
                            
                            self.chat.post_message(
                                channel=eval_channel_id,
                                text=message.split('\n')[0],  # İlk satırı text olarak kullan
                                blocks=[
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": message
                                        }
                                    }
                                ]
                            )
                            logger.info(f"[i] Repo bekleme mesajı gönderildi | Evaluation: {evaluation_id}")
                        except Exception as e:
                            logger.warning(f"[!] Repo bekleme mesajı gönderilemedi: {e}")

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

            # Challenge canvas/özet mesajını güncelle
            try:
                await self.update_challenge_canvas(evaluation["challenge_hub_id"])
            except Exception as e:
                logger.warning(f"[!] GitHub linki sonrasında canvas güncellenemedi: {e}")

            # Eğer repo public ve 3 kişi oy verdiyse admin onayı iste
            if is_public:
                votes = self.evaluator_repo.get_votes(evaluation_id)
                total_votes = votes["true"] + votes["false"]
                
                if total_votes >= 3:
                    logger.info(f"[+] GitHub repo public ve 3 oy var → Admin onayı bekleniyor | Evaluation: {evaluation_id}")
                    
                    # Kanala admin onay butonu gönder
                    eval_channel_id = evaluation.get("evaluation_channel_id")
                    if eval_channel_id:
                        try:
                            self.chat.post_message(
                                channel=eval_channel_id,
                                text="✅ GitHub public! Admin onayı bekleniyor...",
                                blocks=[
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": (
                                                f"✅ *GitHub Public!*\n"
                                                f"📊 Oylar: ✅{votes['true']} ❌{votes['false']} | 🔗 {github_url}\n\n"
                                                "👤 Admin onayı bekleniyor..."
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
                                                    "text": "✅ Onayla ve Bitir",
                                                    "emoji": True
                                                },
                                                "style": "primary",
                                                "action_id": "admin_approve_evaluation",
                                                "value": evaluation_id
                                            },
                                            {
                                                "type": "button",
                                                "text": {
                                                    "type": "plain_text",
                                                    "text": "❌ Reddet ve Bitir",
                                                    "emoji": True
                                                },
                                                "style": "danger",
                                                "action_id": "admin_reject_evaluation",
                                                "value": evaluation_id
                                            }
                                        ]
                                    }
                                ]
                            )
                            logger.info(f"[i] Admin onay butonu gönderildi | Evaluation: {evaluation_id}")
                        except Exception as e:
                            logger.warning(f"[!] Admin onay butonu gönderilemedi: {e}")
                    
                    return {
                        "success": True,
                        "message": f"✅ GitHub public! Admin onayı bekleniyor: {github_url}"
                    }
                else:
                    return {
                        "success": True,
                        "message": f"✅ GitHub public: {github_url}\n💡 Tüm oylar alındığında değerlendirme tamamlanacak."
                    }
            else:
                return {
                    "success": True,
                    "message": f"⚠️ GitHub private: {github_url}\n💡 Public yapın."
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

    async def admin_finalize_evaluation(
        self,
        evaluation_id: str,
        admin_user_id: str,
        approval: str  # "approved" veya "rejected"
    ) -> Dict[str, Any]:
        """
        Admin onayı ile değerlendirmeyi sonlandırır.
        Sadece admin (admin_slack_id) veya workspace owner çağırabilir.
        """
        try:
            settings = get_settings()
            ADMIN_USER_ID = settings.admin_slack_id
            
            # Admin kontrolü: admin_slack_id veya workspace owner
            is_admin = False
            if ADMIN_USER_ID and admin_user_id == ADMIN_USER_ID:
                is_admin = True
                logger.debug(f"[i] Admin kontrolü: admin_slack_id eşleşti | User: {admin_user_id}")
            else:
                # Slack API'den workspace owner kontrolü yap
                try:
                    # self.chat içindeki client'ı kullan
                    if hasattr(self.chat, 'client'):
                        user_info = self.chat.client.users_info(user=admin_user_id)
                        if user_info.get("ok"):
                            user = user_info.get("user", {})
                            is_owner = user.get("is_owner", False)
                            is_admin_flag = user.get("is_admin", False)
                            is_admin = is_owner or is_admin_flag
                            if is_admin:
                                logger.info(f"[i] Admin kontrolü: Workspace owner/admin tespit edildi | User: {admin_user_id} | Owner: {is_owner}, Admin: {is_admin_flag}")
                except Exception as e:
                    logger.warning(f"[!] Workspace owner kontrolü yapılamadı: {e}")
            
            if not is_admin:
                logger.warning(f"[!] Admin yetkisi reddedildi | User: {admin_user_id} | Admin ID: {ADMIN_USER_ID}")
                return {
                    "success": False,
                    "message": "❌ Sadece admin (workspace owner) bu işlemi yapabilir."
                }
            
            evaluation = self.evaluation_repo.get(evaluation_id)
            if not evaluation:
                return {
                    "success": False,
                    "message": "❌ Değerlendirme bulunamadı."
                }
            
            if evaluation.get("status") == "completed":
                return {
                    "success": False,
                    "message": "⚠️ Bu değerlendirme zaten tamamlanmış."
                }
            
            # Admin onayını kaydet
            self.evaluation_repo.update(evaluation_id, {
                "admin_approval": approval
            })
            
            logger.info(f"[+] Admin onayı: {approval} | Evaluation: {evaluation_id} | Admin: {admin_user_id}")
            
            # Değerlendirmeyi finalize et
            await self.finalize_evaluation(evaluation_id, admin_approval=approval)
            
            if approval == "approved":
                return {
                    "success": True,
                    "message": "✅ Değerlendirme admin tarafından onaylandı ve tamamlandı."
                }
            else:
                return {
                    "success": True,
                    "message": "❌ Değerlendirme admin tarafından reddedildi ve tamamlandı."
                }
            
        except Exception as e:
            logger.error(f"[X] Admin finalize hatası: {e}", exc_info=True)
            return {
                "success": False,
                "message": "❌ Admin onayı kaydedilirken bir hata oluştu."
            }

    async def finalize_evaluation(self, evaluation_id: str, admin_approval: str = None):
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

            # Admin reddetmişse otomatik olarak başarısız
            if admin_approval == "rejected":
                final_result = "failed"
                result_message = "❌ *Challenge Başarısız*\n\n*Nedenler:*\n• Admin tarafından reddedildi"
            elif true_votes > false_votes and github_public and github_url:
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
                # Challenge'ın status'unu güncelle (değerlendirme tamamlandı)
                self.hub_repo.update(challenge_id, {
                    "status": "completed",
                    "completed_at": datetime.now().isoformat()
                })
                logger.info(f"[+] Challenge status güncellendi: {challenge_id} | Status: completed")
                
                logger.info(f"[+] Challenge status güncellendi: {challenge_id} | Status: completed")
                
                # Başarı durumunda istatistikleri ve puanları güncelle
                if final_result == "success":
                    try:
                        # Puan miktarı (varsayılan: 100)
                        POINTS_PER_SUCCESS = 100
                        
                        # Katılımcıları al
                        participants = self.participant_repo.get_team_members(challenge_id)
                        participant_ids = [p["user_id"] for p in participants]
                        
                        # Owner'ı al (eğer katılımcılar arasında değilse ekle)
                        creator_id = challenge.get("creator_id")
                        if creator_id and creator_id not in participant_ids:
                            participant_ids.append(creator_id)
                        
                        # Herkese puan ver ve başarı sayısını artır
                        for user_id in participant_ids:
                            self.stats_repo.add_points(user_id, POINTS_PER_SUCCESS)
                            self.stats_repo.increment_completed(user_id)
                            logger.info(f"[+] Puan ve başarı güncellendi: {user_id} | Challenge: {challenge_id}")
                            
                    except Exception as e:
                        logger.error(f"[X] Başarı istatistikleri güncellenirken hata: {e}", exc_info=True)

                # Sonuç mesajını hem challenge kanalına hem ana kanala gönder
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
                
                # Ana kanala (hub_channel_id) sonuç mesajı gönder
                hub_channel_id = challenge.get("hub_channel_id")
                if hub_channel_id:
                    try:
                        self.chat.post_message(
                            channel=hub_channel_id,
                            text=result_message,
                            blocks=result_blocks
                        )
                        logger.info(f"[+] Değerlendirme sonucu ana kanala gönderildi: {hub_channel_id}")
                    except Exception as e:
                        logger.warning(f"[!] Ana kanala sonuç mesajı gönderilemedi: {e}")
                
                # Challenge kanalına da gönder (kanal arşivlenmiş olabilir, hata kontrolü yap)
                challenge_channel_id = challenge.get("challenge_channel_id")
                if challenge_channel_id:
                    try:
                        # Admin onay/red bilgisi ekle
                        admin_decision_text = ""
                        if admin_approval == "approved":
                            admin_decision_text = "\n\n👤 *Admin Kararı:* ✅ Onaylandı"
                        elif admin_approval == "rejected":
                            admin_decision_text = "\n\n👤 *Admin Kararı:* ❌ Reddedildi"
                        
                        # Kanal kapanma zamanını hesapla (3 saat sonra)
                        close_time = (datetime.now() + timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
                        
                        challenge_result_blocks = result_blocks + [
                            {
                                "type": "divider"
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"⏳ *Önemli Bilgilendirme:*\n"
                                        f"Bu kanal *{close_time}*'de (3 saat sonra) otomatik olarak arşivlenecektir.\n"
                                        f"Lütfen önemli mesajlarınızı bu süre içinde kontrol edin. 📋"
                                    )
                                }
                            }
                        ]
                        
                        # İlk section'a admin kararını ekle
                        if admin_decision_text:
                            challenge_result_blocks[0]["text"]["text"] += admin_decision_text
                        
                        self.chat.post_message(
                            channel=challenge_channel_id,
                            text=result_message,
                            blocks=challenge_result_blocks
                        )
                        logger.info(f"[+] Challenge kanalına sonuç mesajı gönderildi: {challenge_channel_id}")
                    except Exception as e:
                        logger.warning(f"[!] Challenge kanalına sonuç mesajı gönderilemedi (kanal arşivlenmiş olabilir): {e}")
            # Değerlendirme kanalına bitiş mesajı gönder ve 1 saat sonra kapat
            eval_channel_id = evaluation.get("evaluation_channel_id")
            if eval_channel_id:
                try:
                    # Kapanma saatini hesapla
                    close_time = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")

                    self.chat.post_message(
                        channel=eval_channel_id,
                        text="🏁 *Değerlendirme Tamamlandı!*",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"🏁 *Değerlendirme süreci sona erdi.*\n\n"
                                        f"Sonuçları yukarıdaki mesajdan veya ana kanaldan takip edebilirsiniz.\n\n"
                                        f"⏳ *Önemli:* Bu kanal saat *{close_time}*'de (1 saat sonra) otomatik olarak arşivlenecektir. Bu süre zarfında mesajları kontrol edebilirsiniz. 👋"
                                    )
                                }
                            }
                        ]
                    )

                    # Kanalı 1 saat sonra arşivlemek üzere planla
                    delay_hours = 1
                    self.cron.add_once_job(
                        func=self._archive_channel_delayed,
                        delay_minutes=delay_hours * 60,
                        job_id=f"archive_evaluation_{evaluation_id}",
                        args=[evaluation_id, eval_channel_id]
                    )
                    logger.info(f"[+] Değerlendirme kanalı 1 saat sonra arşivlenmek üzere planlandı (Saat: {close_time}) | ID: {evaluation_id}")
                except Exception as e:
                    logger.warning(f"[!] Değerlendirme kanalı mesaj gönderimi veya arşivleme planı hatası: {e}")

            # Canvas/özet mesajını son durum ile güncelle
            try:
                await self.update_challenge_canvas(challenge_id)
            except Exception as e:
                logger.warning(f"[!] Finalize sonrası canvas güncellenemedi: {e}")

            logger.info(f"[+] Değerlendirme finalize edildi: {evaluation_id} | Sonuç: {final_result}")

        except Exception as e:
            logger.error(f"[X] Değerlendirme finalize hatası: {e}", exc_info=True)

    def _archive_channel_delayed(self, evaluation_id: str, channel_id: str):
        """Kanalı gecikmeli olarak arşivler (Cron tarafından çağrılır)."""
        try:
            success = self.conv.archive_channel(channel_id)
            if success:
                logger.info(f"[+] Değerlendirme kanalı başarıyla arşivlendi: {channel_id} | Evaluation: {evaluation_id}")
            else:
                logger.warning(f"[!] Değerlendirme kanalı arşivlenemedi: {channel_id} | Evaluation: {evaluation_id}")
        except Exception as e:
            logger.error(f"[X] Gecikmeli değerlendirme kanalı arşivleme hatası: {e} | Kanal: {channel_id}")

    async def force_complete_evaluation(self, evaluation_id: str, admin_user_id: str, result: str) -> Dict[str, Any]:
        """
        Admin (Owner) tarafından değerlendirmeyi zorla bitirir.
        result: 'success' veya 'failed'
        """
        try:
            # Yetki kontrolü
            settings = get_settings()
            ADMIN_USER_ID = settings.admin_slack_id
            
            if admin_user_id != ADMIN_USER_ID:
                return {"success": False, "message": "❌ Yetkisiz işlem."}

            evaluation = self.evaluation_repo.get(evaluation_id)
            if not evaluation:
                return {"success": False, "message": "❌ Değerlendirme bulunamadı."}

            challenge_id = evaluation["challenge_hub_id"]

            # Sonucu ayarla
            final_result = result
            result_message = ""
            if result == "success":
                result_message = "🎉 *Challenge Başarılı!* (Yönetici Kararı)"
            else:
                result_message = "❌ *Challenge Başarısız* (Yönetici Kararı)"

            # DB güncelle
            self.evaluation_repo.update(evaluation_id, {
                "status": "completed",
                "final_result": final_result,
                "completed_at": datetime.now().isoformat()
            })

            self.hub_repo.update(
                challenge_id,
                {
                    "status": "completed",
                    "completed_at": datetime.now().isoformat(),
                },
            )

            # Başarı durumunda istatistikleri ve puanları güncelle (Force Complete için de)
            if final_result == "success":
                try:
                    POINTS_PER_SUCCESS = 100
                    challenge = self.hub_repo.get(challenge_id)
                    participants = self.participant_repo.get_team_members(challenge_id)
                    participant_ids = [p["user_id"] for p in participants]
                    creator_id = challenge.get("creator_id") if challenge else None
                    if creator_id and creator_id not in participant_ids:
                        participant_ids.append(creator_id)

                    for user_id in participant_ids:
                        self.stats_repo.add_points(user_id, POINTS_PER_SUCCESS)
                        self.stats_repo.increment_completed(user_id)
                        logger.info(f"[+] Force success: Puan ve başarı güncellendi: {user_id}")
                except Exception as e:
                    logger.error(f"[X] Force success istatistikleri güncellenirken hata: {e}")

            # Canvas/özet mesajını güncelle
            try:
                await self.update_challenge_canvas(challenge_id)
            except Exception as e:
                logger.warning(f"[!] Force complete sonrası canvas güncellenemedi: {e}")

            # Bildirim gönder (hem evaluation hem challenge kanallarına)
            eval_channel_id = evaluation.get("evaluation_channel_id")
            if eval_channel_id:
                try:
                    # Kapanma saatini hesapla
                    close_time = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")
                    
                    self.chat.post_message(
                        channel=eval_channel_id,
                        text=result_message,
                        blocks=[
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": f"{result_message}\n\n👤 İşlemi Yapan: <@{admin_user_id}>"}
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"⏳ *Önemli:* Bu kanal saat *{close_time}* civarında (1 saat sonra) otomatik olarak arşivlenecektir. 👋"
                                }
                            }
                        ]
                    )
                    
                    # Kanalı 1 saat sonra arşivlemek üzere planla
                    self.cron.add_once_job(
                        func=self._archive_channel_delayed,
                        delay_minutes=60,
                        job_id=f"archive_evaluation_force_{evaluation_id}",
                        args=[evaluation_id, eval_channel_id]
                    )
                    logger.info(f"[+] Değerlendirme kanalı zorla kapatma sonrası 1 saat sonra arşivlenecek | ID: {evaluation_id}")
                except Exception as e:
                    logger.warning(f"[!] Force complete mesaj/arşiv planlama hatası: {e}")
            
            # Challenge kanalına da bilgilendirme mesajı gönder
            challenge = self.hub_repo.get(challenge_id)
            if challenge:
                challenge_channel_id = challenge.get("challenge_channel_id")
                if challenge_channel_id:
                    try:
                        # Challenge kanalı kapanma zamanı (3 saat sonra)
                        challenge_close_time = (datetime.now() + timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
                        
                        self.chat.post_message(
                            channel=challenge_channel_id,
                            text=result_message,
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn", 
                                        "text": f"{result_message}\n\n👤 *Admin Kararı:* <@{admin_user_id}> tarafından manuel olarak sonuçlandırıldı"
                                    }
                                },
                                {
                                    "type": "divider"
                                },
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": (
                                            f"⏳ *Önemli Bilgilendirme:*\n"
                                            f"Bu kanal *{challenge_close_time}*'de (3 saat sonra) otomatik olarak arşivlenecektir.\n"
                                            f"Lütfen önemli mesajlarınızı bu süre içinde kontrol edin. 📋"
                                        )
                                    }
                                }
                            ]
                        )
                        logger.info(f"[+] Force complete: Challenge kanalına sonuç mesajı gönderildi: {challenge_channel_id}")
                    except Exception as e:
                        logger.warning(f"[!] Force complete: Challenge kanalına mesaj gönderilemedi: {e}")

            return {
                "success": True, 
                "message": f"✅ Değerlendirme zorla bitirildi: {result.upper()}"
            }

        except Exception as e:
            logger.error(f"[X] Force complete error: {e}")
            return {"success": False, "message": "❌ İşlem sırasında hata oluştu."}
