"""Senders the inbox poller never turns into work.

Security notices, mailing-list digests and other system mail land in the
purchasing mailbox too. Matching the sender against a small list of
addresses and domains keeps them out of the cases, so nobody gets an
escalation about "New sign-in detected". The list comes from the Control
Tower settings (``ignored_senders``) with the environment as the baseline.
"""

from __future__ import annotations

from collections.abc import Iterable


def normalize_pattern(pattern: str) -> str:
    """``*@Domain.com`` / ``@domain.com`` / ``domain.com`` all mean the domain; addresses stay."""
    text = pattern.strip().lower()
    if text.startswith("*@"):
        text = text[2:]
    return text.lstrip("@")


def is_ignored(address: str | None, patterns: Iterable[str]) -> bool:
    """True when ``address`` is one of the patterns or belongs to one of their domains."""
    if not address:
        return False
    sender = address.strip().lower()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    for raw in patterns:
        pattern = normalize_pattern(raw)
        if not pattern:
            continue
        if "@" in pattern:
            if sender == pattern:
                return True
        elif domain == pattern or domain.endswith("." + pattern):
            return True
    return False
