"""
Command handler'ları için modül.
"""

from .daily_handler import setup_daily_handlers
from .coffee_handler import setup_coffee_handlers
from .poll_handler import setup_poll_handlers
from .feedback_handler import setup_feedback_handlers
from .knowledge_handler import setup_knowledge_handlers
from .profile_handler import setup_profile_handlers
from .health_handler import setup_health_handlers
from .help_handler import setup_help_handlers
from .statistics_handler import setup_statistics_handlers
from .challenge_handler import setup_challenge_handlers
from .challenge_evaluation_handler import setup_challenge_evaluation_handlers

__all__ = [
    'setup_daily_handlers',
    'setup_coffee_handlers',
    'setup_poll_handlers',
    'setup_feedback_handlers',
    'setup_knowledge_handlers',
    'setup_profile_handlers',
    'setup_health_handlers',
    'setup_help_handlers',
    'setup_statistics_handlers',
    'setup_challenge_handlers',
    'setup_challenge_evaluation_handlers',
]
