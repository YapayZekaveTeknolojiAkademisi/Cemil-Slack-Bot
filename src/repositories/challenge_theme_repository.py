from src.repositories.base_repository import BaseRepository
from src.clients.database_client import DatabaseClient


class ChallengeThemeRepository(BaseRepository):
    """Challenge temaları için veritabanı erişim sınıfı."""

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, "challenge_themes")

    def get_active_themes(self):
        """Aktif temaları getirir."""
        return self.list(filters={"is_active": 1})
