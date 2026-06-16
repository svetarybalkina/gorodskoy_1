import logging
from dataclasses import dataclass

from app.core.config import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotRunState:
    enabled: bool
    reason: str


def run_bot_if_configured(settings: Settings) -> BotRunState:
    if not settings.telegram_bot_token:
        reason = "TELEGRAM_BOT_TOKEN is not set; Telegram bot is disabled."
        logger.info(reason)
        return BotRunState(enabled=False, reason=reason)

    reason = "Telegram bot token is configured; bot startup will be implemented in task 8."
    logger.info(reason)
    return BotRunState(enabled=True, reason=reason)
