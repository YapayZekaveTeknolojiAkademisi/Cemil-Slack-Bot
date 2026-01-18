import random
from typing import Optional, List, Dict, Any
from src.repositories.base_repository import BaseRepository
from src.clients.database_client import DatabaseClient
from src.core.logger import logger


class ChallengeProjectRepository(BaseRepository):
    """Challenge proje şablonları için veritabanı erişim sınıfı."""

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, "challenge_projects")

    def get_by_theme(self, theme: str) -> List[Dict[str, Any]]:
        """Tema bazlı projeleri getirir."""
        return self.list(filters={"theme": theme})

    def get_random_project(self, theme: str) -> Optional[Dict[str, Any]]:
        """Tema bazlı random proje seçer."""
        projects = self.get_by_theme(theme)
        if not projects:
            return None
        return random.choice(projects)

    def get_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        """ID ile proje getirir."""
        return self.get(project_id)
