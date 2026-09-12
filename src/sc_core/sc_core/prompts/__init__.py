"""Prompt management: Langfuse-managed text with a committed local fallback.

``get_prompt("supplier_tone")`` returns the production-labelled version from
Langfuse when it is reachable (cached), otherwise the file
``sc_core/prompts/local/supplier_tone.md``. The returned object knows its
version so the chat client can record which prompt produced a generation.

Shared fragments (tone, formats, escalation policy) live here; each agent's
own prompts live in its package and use the same helper with a directory
argument.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from sc_core.infra.settings import LangfuseCfg, get_settings
from sc_core.infra.tracing import tracer
from sc_core.shared.errors import ConfigurationError

LOCAL_DIR = Path(__file__).with_name("local")
_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    version: str  # Langfuse version number, or "local"
    source: str  # "langfuse" | "local"
    langfuse_prompt: Any = None  # the Langfuse client object, for generation linkage

    def compile(self, **variables: Any) -> str:
        """Substitute ``{{name}}`` placeholders; unknown placeholders are left in place."""

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            return str(variables[key]) if key in variables else match.group(0)

        return _VAR.sub(replace, self.text)

    @property
    def variables(self) -> list[str]:
        return sorted(set(_VAR.findall(self.text)))


def get_prompt(
    name: str,
    *,
    local_dir: Path | None = None,
    label: str = "production",
    cfg: LangfuseCfg | None = None,
) -> Prompt:
    """Langfuse first, local file second. Raises when neither exists."""
    cfg = cfg or get_settings().langfuse
    local_text = _read_local(name, local_dir or LOCAL_DIR)
    if cfg.configured:
        try:
            client = tracer().get_prompt(
                name,
                label=label,
                type="text",
                cache_ttl_seconds=cfg.prompt_cache_seconds,
                fallback=local_text,
            )
            text = getattr(client, "prompt", None)
            version = getattr(client, "version", None)
            if isinstance(text, str) and text and version is not None:
                return Prompt(name, text, str(version), "langfuse", client)
        except Exception as exc:  # noqa: BLE001 - any failure falls back to the local file
            logger.bind(prompt=name, reason=type(exc).__name__).warning(
                "langfuse prompt unavailable; using local fallback"
            )
    if local_text is None:
        raise ConfigurationError(f"prompt {name!r} exists neither in Langfuse nor locally")
    return Prompt(name, local_text, "local", "local")


def _read_local(name: str, directory: Path) -> str | None:
    path = directory / f"{name}.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()
