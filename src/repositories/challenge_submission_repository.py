from src.repositories.base_repository import BaseRepository
from src.clients.database_client import DatabaseClient


class ChallengeSubmissionRepository(BaseRepository):
    """Challenge submission'ları için veritabanı erişim sınıfı."""

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, "challenge_submissions")

    def get_by_challenge(self, challenge_hub_id: str):
        """Challenge'a ait submission'ı getirir."""
        submissions = self.list(filters={"challenge_hub_id": challenge_hub_id})
        return submissions[0] if submissions else None
