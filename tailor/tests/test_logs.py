"""The API key never reaches tailor's logs, even inside a traceback."""

import io
import logging

from tailor.logs import REDACTED, RedactSecrets

KEY = "sk-ant-api03-not-a-real-key"


def test_the_api_key_is_redacted_from_messages_and_tracebacks():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactSecrets([KEY]))
    logger = logging.getLogger("test-tailor-redaction")
    logger.addHandler(handler)
    logger.propagate = False
    logger.warning("Calling the model with %s", KEY)
    try:
        raise RuntimeError(f"x-api-key: {KEY}")
    except RuntimeError:
        logger.exception("Model call failed")
    written = stream.getvalue()
    assert KEY not in written
    assert written.count(REDACTED) == 2
