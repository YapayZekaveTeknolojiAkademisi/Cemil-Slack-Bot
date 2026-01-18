from typing import Optional, Dict, Any
from src.repositories.base_repository import BaseRepository
from src.clients.database_client import DatabaseClient
from src.core.logger import logger


class UserChallengeStatsRepository(BaseRepository):
    """Kullanıcı challenge istatistikleri için veritabanı erişim sınıfı."""

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, "user_challenge_stats")

    def get_or_create(self, user_id: str) -> Dict[str, Any]:
        """Kullanıcı istatistiklerini getirir, yoksa oluşturur."""
        stats = self.get(user_id)
        if not stats:
            # Yeni kayıt oluştur
            self.create({
                "user_id": user_id,
                "total_challenges": 0,
                "completed_challenges": 0,
                "total_points": 0
            })
            stats = self.get(user_id)
        return stats

    def add_points(self, user_id: str, points: int):
        """Kullanıcıya puan ekler."""
        stats = self.get_or_create(user_id)
        new_total = stats.get("total_points", 0) + points
        self.update(user_id, {"total_points": new_total})

    def increment_completed(self, user_id: str):
        """Tamamlanan challenge sayısını artırır."""
        stats = self.get_or_create(user_id)
        new_count = stats.get("completed_challenges", 0) + 1
        self.update(user_id, {"completed_challenges": new_count})
