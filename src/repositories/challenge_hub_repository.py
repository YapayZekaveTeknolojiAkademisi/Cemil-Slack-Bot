from typing import Optional, List, Dict, Any
from src.repositories.base_repository import BaseRepository
from src.clients.database_client import DatabaseClient
from src.core.logger import logger


class ChallengeHubRepository(BaseRepository):
    """Challenge Hub'lar için veritabanı erişim sınıfı."""

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, "challenge_hubs")

    def get_active_challenge(self) -> Optional[Dict[str, Any]]:
        """Aktif (recruiting veya active) challenge getirir."""
        try:
            with self.db_client.get_connection() as conn:
                cursor = conn.cursor()
                sql = """
                    SELECT * FROM challenge_hubs
                    WHERE status IN ('recruiting', 'active')
                    ORDER BY created_at DESC
                    LIMIT 1
                """
                cursor.execute(sql)
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"[X] get_active_challenge hatası: {e}")
            return None

    def get_by_theme(self, theme: str) -> List[Dict[str, Any]]:
        """Tema bazlı challenge'ları getirir."""
        return self.list(filters={"theme": theme})

    def get_all_active(self) -> List[Dict[str, Any]]:
        """Tüm aktif challenge'ları getirir."""
        try:
            with self.db_client.get_connection() as conn:
                cursor = conn.cursor()
                sql = """
                    SELECT * FROM challenge_hubs
                    WHERE status IN ('recruiting', 'active')
                    ORDER BY created_at DESC
                """
                cursor.execute(sql)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[X] get_all_active hatası: {e}")
            return []
