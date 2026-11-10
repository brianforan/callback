"""Logging for every backend process, with the configured secrets kept out of every line.

Board API keys sit in URLs (Jooble's is in the path, The Muse's in the query), so an error that
quotes its request, or a traceback, could otherwise write one to the log. Each handler gets a
filter that replaces every configured secret, in the message, the traceback and the stack.
"""

import logging
from collections.abc import Iterable, Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.config import Settings, get_settings

REDACTED = "[redacted]"
# Shorter values would match ordinary text, and no real key, token or password is this short.
_MIN_SECRET_LENGTH = 6
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class RedactSecrets(logging.Filter):
    """Replaces each secret in a record before any handler writes it."""

    def __init__(self, secrets: Iterable[str | None]) -> None:
        super().__init__()
        # Longest first, so a secret that contains another is replaced whole.
        self._secrets = sorted(
            {secret for secret in secrets if secret and len(secret) >= _MIN_SECRET_LENGTH},
            key=len,
            reverse=True,
        )

    def _clean(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def _has_secret(self, text: str) -> bool:
        return any(secret in text for secret in self._secrets)

    def _clean_arg(self, arg: object) -> object:
        text = str(arg)
        return self._clean(text) if self._has_secret(text) else arg

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        # Checked on the formatted message, so a secret passed as an argument is caught too. The
        # arguments keep their shape and types, and only one holding a secret is replaced, since
        # some formatters read them directly: uvicorn's access log unpacks them and reads the
        # status code as a number.
        if self._has_secret(record.getMessage()):
            if isinstance(record.msg, str):
                record.msg = self._clean(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(self._clean_arg(arg) for arg in record.args)
            elif isinstance(record.args, Mapping):
                record.args = {key: self._clean_arg(value) for key, value in record.args.items()}
            # A secret split between the template and an argument is still in the message.
            if self._has_secret(record.getMessage()):
                record.msg = self._clean(record.getMessage())
                record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._clean(record.exc_text)
        if record.stack_info:
            record.stack_info = self._clean(record.stack_info)
        return True


def secrets_of(settings: Settings) -> list[str | None]:
    """Every secret the backend is configured with."""
    try:
        database_password = make_url(settings.database_url).password
    except ArgumentError:
        database_password = None
    return [
        settings.secret_key,
        database_password,
        settings.jooble_api_key,
        settings.findwork_api_key,
        settings.muse_api_key,
        settings.anthropic_admin_api_key,
        settings.anthropic_api_key,
    ]


def _handlers() -> list[logging.Handler]:
    handlers = list(logging.root.handlers)
    for logger in logging.root.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            handlers.extend(logger.handlers)
    return handlers


def configure_logging(level: str | int | None = None, fmt: str = _FORMAT, stream=None) -> None:
    """Set up logging for this process and redact secrets on every handler it has.

    Uvicorn sets up its own handlers before the app is imported, so those are covered too.
    """
    settings = get_settings()
    logging.basicConfig(level=level or settings.log_level, format=fmt, stream=stream)
    redact = RedactSecrets(secrets_of(settings))
    for handler in _handlers():
        if not any(isinstance(existing, RedactSecrets) for existing in handler.filters):
            handler.addFilter(redact)
