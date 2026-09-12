"""Publish the local prompt files to Langfuse Prompt Management (idempotent).

Every ``*.md`` under ``sc_core/prompts/local``, ``supplier_comms/prompts``
and ``director/prompts`` becomes a text prompt named after the file, with
the ``production`` label ``get_prompt`` asks for. A prompt whose production
text already matches the file is left alone; otherwise a new version is
created and labelled. Requires ``SC__LANGFUSE__*`` in ``.env`` and the
Langfuse container from ``just up``.

Usage: ``just langfuse-prompts`` (or ``uv run python scripts/langfuse_prompts.py [--dry-run]``).
"""

from __future__ import annotations

import argparse
import sys

from members import SRC

from sc_core.infra.settings import Settings

PROMPT_DIRS = [
    SRC / "sc_core" / "sc_core" / "prompts" / "local",
    SRC / "supplier_comms" / "supplier_comms" / "prompts",
    SRC / "director" / "director" / "prompts",
]
LABEL = "production"


def local_prompts() -> dict[str, str]:
    prompts: dict[str, str] = {}
    for directory in PROMPT_DIRS:
        for path in sorted(directory.glob("*.md")):
            if path.stem in prompts:
                raise SystemExit(f"duplicate prompt name {path.stem!r} ({path})")
            prompts[path.stem] = path.read_text(encoding="utf-8").strip()
    return prompts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="only report what would change")
    args = parser.parse_args(argv)

    settings = Settings()
    cfg = settings.langfuse
    if not cfg.configured:
        raise SystemExit("SC__LANGFUSE__PUBLIC_KEY / SECRET_KEY not set in .env")
    from langfuse import Langfuse

    client = Langfuse(
        public_key=cfg.public_key, secret_key=cfg.secret_key.get_secret_value(), host=cfg.host
    )
    created = unchanged = 0
    for name, text in local_prompts().items():
        current: str | None = None
        try:
            existing = client.get_prompt(name, label=LABEL, type="text", cache_ttl_seconds=0)
            current = getattr(existing, "prompt", None)
        except Exception:  # noqa: BLE001 - not found (or unreachable, which fails on create)
            current = None
        if current is not None and current.strip() == text:
            unchanged += 1
            print(f"= {name} (up to date)")
            continue
        action = "would create" if args.dry_run else "created"
        if not args.dry_run:
            client.create_prompt(name=name, prompt=text, labels=[LABEL], type="text")
        created += 1
        print(f"+ {name} ({action} version, label {LABEL})")
    client.flush()
    print(f"{created} created, {unchanged} unchanged at {cfg.host}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
