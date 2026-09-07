"""Tests for sc_core.infra.logger."""

import io
import json
import logging

import pytest
from loguru import logger

from sc_core.infra import context
from sc_core.infra.logger import REDACTED, configure_logging, is_sensitive_key, redact
from sc_core.infra.settings import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"service_name": "test-svc", "environment": "test"}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _json_lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _reset_logger() -> None:
    yield  # type: ignore[misc]
    logger.remove()
    logger.configure(patcher=None)


@pytest.mark.parametrize(
    ("key", "sensitive"),
    [
        ("body", True),
        ("html_body", True),
        ("inbound_text", True),
        ("API_KEY", True),
        ("client_secret", True),
        ("Authorization", True),
        ("access_token", True),
        ("po_name", False),
        ("context", False),
        ("case_id", False),
    ],
)
def test_is_sensitive_key(key: str, sensitive: bool) -> None:
    assert is_sensitive_key(key) is sensitive


def test_redact_is_recursive_and_non_destructive() -> None:
    original = {
        "po": "PO1",
        "mail": {"subject": "s", "body": "secret text"},
        "list": [{"token": "x"}],
    }
    result = redact(original)
    assert result == {
        "po": "PO1",
        "mail": {"subject": "s", "body": REDACTED},
        "list": [{"token": REDACTED}],
    }
    assert original["mail"]["body"] == "secret text"  # input untouched


def test_json_logs_include_context_and_redact_extras() -> None:
    stream = io.StringIO()
    configure_logging(_settings(), stream=stream)
    with context.bind(case_id="case-1", trace_id="trace-1"):
        logger.bind(po_name="PO00123", api_key="sk-live-123", payload={"body": "hola"}).info(
            "hello"
        )
    (line,) = _json_lines(stream)
    assert line["msg"] == "hello"
    assert line["level"] == "INFO"
    assert line["service_name"] == "test-svc"
    assert line["case_id"] == "case-1"
    assert line["trace_id"] == "trace-1"
    assert line["extra"] == {
        "po_name": "PO00123",
        "api_key": REDACTED,
        "payload": {"body": REDACTED},
    }
    assert "sk-live-123" not in stream.getvalue()
    assert "hola" not in stream.getvalue()


def test_human_logs_in_dev_contain_context() -> None:
    stream = io.StringIO()
    configure_logging(_settings(environment="dev"), stream=stream)
    with context.bind(case_id="case-9"):
        logger.bind(secret="s3cr3t", po="PO7").warning("careful")
    out = stream.getvalue()
    assert "careful" in out
    assert "test-svc" in out
    assert "case-9" in out
    assert "PO7" in out
    assert "s3cr3t" not in out


def test_log_level_is_respected() -> None:
    stream = io.StringIO()
    configure_logging(_settings(log_level="WARNING"), stream=stream)
    logger.info("hidden")
    logger.error("shown")
    lines = _json_lines(stream)
    assert [entry["msg"] for entry in lines] == ["shown"]


def test_stdlib_logging_is_intercepted() -> None:
    stream = io.StringIO()
    configure_logging(_settings(), stream=stream)
    logging.getLogger("uvicorn.error").warning("from stdlib")
    (line,) = _json_lines(stream)
    assert line["msg"] == "from stdlib"
    assert line["level"] == "WARNING"


def test_configure_twice_does_not_duplicate_output() -> None:
    stream = io.StringIO()
    configure_logging(_settings(), stream=stream)
    configure_logging(_settings(), stream=stream)
    logger.info("once")
    assert len(_json_lines(stream)) == 1
