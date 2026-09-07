"""Tests for sc_core.shared.errors."""

import pytest

from sc_core.infra import exception as infra_exception
from sc_core.shared.errors import (
    BusinessRuleViolation,
    ExternalServiceError,
    NotFound,
    ScError,
    ValidationFailed,
)


def test_hierarchy_and_defaults() -> None:
    err = NotFound("PO00123 not found", details={"model": "purchase.order"})
    assert isinstance(err, ScError)
    assert err.code == "not_found"
    assert err.retryable is False
    assert err.to_dict() == {
        "code": "not_found",
        "message": "PO00123 not found",
        "retryable": False,
        "details": {"model": "purchase.order"},
    }
    assert str(err) == "PO00123 not found"


def test_external_service_error_is_retryable_by_default() -> None:
    err = ExternalServiceError("timeout", service="odoo", details={"status": 503})
    assert err.retryable is True
    assert err.service == "odoo"
    assert err.details == {"service": "odoo", "status": 503}


def test_external_service_error_can_be_marked_permanent() -> None:
    err = ExternalServiceError("bad request", service="graph", retryable=False)
    assert err.retryable is False


@pytest.mark.parametrize("cls", [ValidationFailed, BusinessRuleViolation])
def test_business_errors_are_never_retryable(cls: type[ScError]) -> None:
    assert cls("x").retryable is False


def test_infra_exception_reexports_same_classes() -> None:
    assert infra_exception.NotFound is NotFound
    assert infra_exception.ScError is ScError
