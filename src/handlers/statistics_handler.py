"""
Admin istatistik komut handler'ları.
"""

from slack_bolt import App
from src.core.logger import logger
from src.commands import ChatManager
from src.services import StatisticsService
from src.repositories import (
    UserRepository,
    ChallengeHubRepository,
    ChallengeParticipantRepository,
    ChallengeEvaluationRepository
)
from src.clients import DatabaseClient
from src.core.settings import get_settings


def is_admin(app: App, user_id: str) -> bool:
    """Kullanıcının admin olup olmadığını kontrol eder."""
    try:
        res = app.client.users_info(user=user_id)
        if res["ok"]:
            user = res["user"]
            return user.get("is_admin", False) or user.get("is_owner", False)
    except Exception as e:
        logger.error(f"[X] Yetki kontrolü hatası: {e}")
    return False


def setup_statistics_handlers(
    app: App,
    statistics_service: StatisticsService,
    chat_manager: ChatManager,
    user_repo: UserRepository
):
    """Admin istatistik handler'larını kaydeder."""
    
    @app.command("/admin-istatistik")
    def handle_admin_statistics(ack, body):
        """Admin istatistiklerini gösterir (Sadece adminler)."""
        ack()
        user_id = body["user_id"]
        channel_id = body["channel_id"]
        
        # Kullanıcı bilgisini al
        try:
            user_data = user_repo.get_by_slack_id(user_id)
            user_name = user_data.get('full_name', user_id) if user_data else user_id
        except Exception:
            user_name = user_id
        
        logger.info(f"[>] /admin-istatistik komutu geldi | Kullanıcı: {user_name} ({user_id})")
        
        # Admin kontrolü
        if not is_admin(app, user_id):
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="🚫 Bu komutu sadece adminler kullanabilir."
            )
            logger.warning(f"[!] Yetkisiz erişim denemesi | Kullanıcı: {user_name} ({user_id})")
            return
        
        try:
            # İstatistikleri topla
            stats = statistics_service.get_all_statistics()
            
            # Formatlanmış rapor oluştur
            report = statistics_service.format_statistics_report(stats)
            
            # Kullanıcıya gönder
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=report
            )
            
            logger.info(f"[+] İstatistikler gösterildi | Kullanıcı: {user_name} ({user_id})")
            
        except Exception as e:
            logger.error(f"[X] İstatistik hatası: {e}", exc_info=True)
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="❌ İstatistikler alınırken bir hata oluştu. Lütfen logları kontrol edin."
            )

    @app.command("/admin-basarili-projeler")
    def handle_admin_successful_projects(ack, body):
        """Başarılı challenge projelerini listeler (Sadece adminler)."""
        ack()
        user_id = body["user_id"]
        channel_id = body["channel_id"]
        
        # Kullanıcı bilgisini al
        try:
            user_data = user_repo.get_by_slack_id(user_id)
            user_name = user_data.get('full_name', user_id) if user_data else user_id
        except Exception:
            user_name = user_id
        
        logger.info(f"[>] /admin-basarili-projeler komutu geldi | Kullanıcı: {user_name} ({user_id})")
        
        # Admin kontrolü
        if not is_admin(app, user_id):
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="🚫 Bu komutu sadece adminler kullanabilir."
            )
            logger.warning(f"[!] Yetkisiz erişim denemesi | Kullanıcı: {user_name} ({user_id})")
            return
        
        try:
            # Başarılı projeleri getir
            settings = get_settings()
            db_client = DatabaseClient(db_path=settings.database_path)
            eval_repo = ChallengeEvaluationRepository(db_client)
            hub_repo = ChallengeHubRepository(db_client)
            participant_repo = ChallengeParticipantRepository(db_client)
            
            # Başarılı değerlendirmeleri bul
            successful_evaluations = eval_repo.list(filters={"final_result": "success"})
            
            if not successful_evaluations:
                chat_manager.post_ephemeral(
                    channel=channel_id,
                    user=user_id,
                    text="ℹ️ Henüz başarılı proje yok."
                )
                return
            
            # Her proje için detayları topla
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🎉 Başarılı Projeler ({len(successful_evaluations)})",
                        "emoji": True
                    }
                },
                {"type": "divider"}
            ]
            
            for eval_data in successful_evaluations:
                challenge_id = eval_data["challenge_hub_id"]
                challenge = hub_repo.get(challenge_id)
                
                if not challenge:
                    continue
                
                # Takım üyelerini al
                participants = participant_repo.get_team_members(challenge_id)
                creator_id = challenge.get("creator_id")
                
                team_members = []
                if creator_id:
                    team_members.append(f"<@{creator_id}>")
                
                for participant in participants:
                    user_id_p = participant.get("user_id")
                    team_members.append(f"<@{user_id_p}>")
                
                # GitHub linki
                github_url = eval_data.get("github_repo_url")
                github_text = f"🔗 <{github_url}|GitHub>" if github_url else "❌ Link yok"
                
                # Tarih bilgisi
                completed_at = eval_data.get("completed_at")
                date_text = "Bilinmiyor"
                if completed_at:
                    from datetime import datetime
                    try:
                        dt = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                        date_text = dt.strftime("%d.%m.%Y")
                    except:
                        pass
                
                # Proje bilgileri
                theme = challenge.get("theme", "N/A")
                project_id = challenge.get("selected_project_id")
                project_name = "N/A"
                if project_id:
                    from src.repositories import ChallengeProjectRepository
                    project_repo = ChallengeProjectRepository(db_client)
                    project = project_repo.get(project_id)
                    if project:
                        project_name = project.get("name", "N/A")
                
                # Block oluştur - daha kompakt
                project_text = (
                    f"*{theme}* | {project_name}\n"
                    f"👥 {', '.join(team_members)}\n"
                    f"{github_text} | 📅 {date_text}"
                )
                
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": project_text
                    }
                })
                blocks.append({"type": "divider"})
            
            # Mesajı gönder
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text=f"🎉 Başarılı Projeler ({len(successful_evaluations)})",
                blocks=blocks
            )
            
            logger.info(f"[+] Başarılı projeler gösterildi | Kullanıcı: {user_name} ({user_id}) | Toplam: {len(successful_evaluations)}")
            
        except Exception as e:
            logger.error(f"[X] Başarılı projeler hatası: {e}", exc_info=True)
            chat_manager.post_ephemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Başarılı projeler alınırken bir hata oluştu. Lütfen logları kontrol edin."
            )