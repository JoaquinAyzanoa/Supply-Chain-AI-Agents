"""Contracts shared by every service."""

from sc_core.schema.base import MutableModel, StrictModel
from sc_core.schema.events import BaseEvent

__all__ = ["BaseEvent", "MutableModel", "StrictModel"]
