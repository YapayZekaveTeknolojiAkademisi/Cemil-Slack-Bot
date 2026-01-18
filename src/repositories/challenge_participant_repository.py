from typing import Optional, List, Dict, Any
from src.repositories.base_repository import BaseRepository
from src.clients.database_client import DatabaseClient
from src.core.logger import logger


class ChallengeParticipantRepository(BaseRepository):
    """Challenge katılımcıları için veritabanı erişim sınıfı."""

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, "challenge_participants")

    def get_by_challenge_and_user(
        self,
        challenge_hub_id: str,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Kullanıcının bu challenge'a katılıp katılmadığını kontrol eder."""
        try:
            with self.db_client.get_connection() as conn:
                cursor = conn.cursor()
                sql = """
                    SELECT * FROM challenge_participants
                    WHERE challenge_hub_id = ? AND user_id = ?
                """
                cursor.execute(sql, (challenge_hub_id, user_id))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"[X] get_by_challenge_and_user hatası: {e}")
            return None

    def get_team_members(self, challenge_hub_id: str) -> List[Dict[str, Any]]:
        """Takım üyelerini getirir."""
        return self.list(filters={"challenge_hub_id": challenge_hub_id})

    def get_user_active_challenges(self, user_id: str) -> List[Dict[str, Any]]:
        """Kullanıcının aktif challenge'larını getirir."""
        try:
            with self.db_client.get_connection() as conn:
                cursor = conn.cursor()
                sql = """
                    SELECT ch.* 
                    FROM challenge_hubs ch
                    INNER JOIN challenge_participants cp 
                        ON ch.id = cp.challenge_hub_id
                    WHERE cp.user_id = ? 
                        AND ch.status IN ('recruiting', 'active')
                """
                cursor.execute(sql, (user_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[X] get_user_active_challenges hatası: {e}")
            return []

    def is_team_full(self, challenge_hub_id: str, team_size: int) -> bool:
        """Takım dolu mu kontrol eder."""
        members = self.get_team_members(challenge_hub_id)
        return len(members) >= team_size
