"""Logging for the tailor service, with the Anthropic API key kept out of every line."""

import logging

from tailor.config import get_settings

REDACTED = "[redacted]"
# Shorter values would match ordinary text, and no real key is this short.
_MIN_SECRET_LENGTH = 6
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class RedactSecrets(logging.Filter):
    """Replaces each secret in a record, its traceback and its stack before it's written."""

    def __init__(self, secrets: list[str | None]) -> None:
        super().__init__()
        self._secrets = sorted(
            {secret for secret in secrets if secret and len(secret) >= _MIN_SECRET_LENGTH},
            key=len,
            reverse=True,
        )

    def _clean(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        record.msg = self._clean(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._clean(record.exc_text)
        if record.stack_info:
            record.stack_info = self._clean(record.stack_info)
        return True


def configure_logging() -> None:
    """Set up logging and redact the API key on every handler, uvicorn's included."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format=_FORMAT)
    redact = RedactSecrets([settings.anthropic_api_key])
    handlers = list(logging.root.handlers)
    for logger in logging.root.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            handlers.extend(logger.handlers)
    for handler in handlers:
        if not any(isinstance(existing, RedactSecrets) for existing in handler.filters):
            handler.addFilter(redact)
