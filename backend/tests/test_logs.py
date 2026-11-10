"""No configured secret is written to a log line, a traceback or a stack trace."""

import io
import logging

from uvicorn.logging import AccessFormatter

from app.config import get_settings
from app.logs import REDACTED, RedactSecrets, secrets_of

SECRET = "jooble-key-0123456789"


def _logger(*secrets, formatter=None):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter or logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RedactSecrets(secrets))
    logger = logging.getLogger(f"test-redaction-{id(stream)}")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def test_a_secret_in_a_message_or_its_arguments_is_redacted():
    logger, stream = _logger(SECRET)
    logger.warning("GET https://jooble.org/api/%s failed", SECRET)
    logger.info(f"Key is {SECRET}")
    written = stream.getvalue()
    assert SECRET not in written
    assert written.count(REDACTED) == 2


def test_a_secret_in_a_traceback_is_redacted():
    logger, stream = _logger(SECRET)
    try:
        raise RuntimeError(f"Could not reach https://jooble.org/api/{SECRET}")
    except RuntimeError:
        logger.exception("Board crashed")
    written = stream.getvalue()
    assert "Traceback" in written
    assert SECRET not in written
    assert REDACTED in written


def _access_logger(*secrets):
    # Uvicorn's access formatter reads the request's parts from the record's arguments.
    formatter = AccessFormatter(
        '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
    )
    return _logger(*secrets, formatter=formatter)


def _log_request(logger, path):
    logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", path, "1.1", 200)


def test_an_access_line_without_a_secret_is_written():
    logger, stream = _access_logger(SECRET)
    _log_request(logger, "/api/health")
    assert stream.getvalue() == '127.0.0.1:5000 - "GET /api/health HTTP/1.1" 200 OK\n'


def test_a_secret_in_an_access_line_is_redacted_and_the_line_still_written():
    logger, stream = _access_logger(SECRET)
    _log_request(logger, f"/api/jobs?key={SECRET}")
    assert stream.getvalue() == f'127.0.0.1:5000 - "GET /api/jobs?key={REDACTED} HTTP/1.1" 200 OK\n'


def test_a_secret_split_across_the_template_and_an_argument_is_redacted():
    logger, stream = _logger(SECRET)
    logger.info("key=jooble-key-%s", "0123456789")
    assert SECRET not in stream.getvalue()
    assert REDACTED in stream.getvalue()


def test_short_or_empty_values_are_not_treated_as_secrets():
    logger, stream = _logger("", None, "abc")
    logger.info("abc stays readable")
    assert stream.getvalue() == "INFO abc stays readable\n"


def test_every_configured_secret_is_covered():
    settings = get_settings().model_copy(
        update={
            "database_url": "postgresql+psycopg://callback:db-password-123@db:5432/callback",
            "jooble_api_key": "jooble-123456",
            "findwork_api_key": "findwork-123456",
            "muse_api_key": "muse-123456",
            "anthropic_api_key": "sk-ant-api-123456",
            "anthropic_admin_api_key": "sk-ant-admin-123456",
        }
    )
    assert set(secrets_of(settings)) >= {
        settings.secret_key,
        "db-password-123",
        "jooble-123456",
        "findwork-123456",
        "muse-123456",
        "sk-ant-api-123456",
        "sk-ant-admin-123456",
    }
