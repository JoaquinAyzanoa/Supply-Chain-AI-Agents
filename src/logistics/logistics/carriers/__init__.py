"""Carrier tracking adapters.

This phase takes the arrival date from the supplier's notice; the interface
is here so a carrier API (DHL, FedEx) can refine it later without touching
the graph. ``NoCarrierTracking`` is the default and the test double.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from sc_core.schema.base import StrictModel


class TrackingInfo(StrictModel):
    carrier: str
    tracking_number: str
    status: str | None = None  # in transit, delivered, exception…
    eta_date: date | None = None
    last_location: str | None = None


@runtime_checkable
class CarrierTracker(Protocol):
    async def track(self, carrier: str | None, tracking_number: str) -> TrackingInfo | None: ...


class NoCarrierTracking:
    """No carrier integration: the notice is the only source."""

    async def track(self, carrier: str | None, tracking_number: str) -> TrackingInfo | None:
        return None


class FakeCarrierTracking:
    """Answers from a table; tests script what the carrier would say."""

    def __init__(self, known: dict[str, TrackingInfo] | None = None) -> None:
        self.known = known or {}
        self.asked: list[tuple[str | None, str]] = []

    async def track(self, carrier: str | None, tracking_number: str) -> TrackingInfo | None:
        self.asked.append((carrier, tracking_number))
        return self.known.get(tracking_number)


__all__ = ["CarrierTracker", "FakeCarrierTracking", "NoCarrierTracking", "TrackingInfo"]
