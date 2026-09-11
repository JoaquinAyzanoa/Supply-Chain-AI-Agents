"""Mail session commands: ``python -m sc_core.mail.cli {login,whoami,logout}``.

Exposed as ``just mail-login`` / ``just mail-whoami`` / ``just mail-logout``.
``login`` applies pending migrations first so the token-cache table exists.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sc_core.infra.logger import configure_logging
from sc_core.infra.migrate import apply_migrations
from sc_core.infra.settings import Settings, get_settings
from sc_core.mail.auth.delegated import DelegatedTokenProvider
from sc_core.mail.auth.store import PostgresTokenCacheStore
from sc_core.mail.errors import MailAuthRequired


def _provider(settings: Settings) -> DelegatedTokenProvider:
    if settings.mail.auth_mode != "delegated":
        raise SystemExit("these commands manage the delegated session only")
    return DelegatedTokenProvider(settings.mail, PostgresTokenCacheStore(str(settings.app_db.dsn)))


def cmd_login(settings: Settings) -> int:
    apply_migrations(str(settings.app_db.dsn), Path(settings.app_db.migrations_dir))
    provider = _provider(settings)
    print("Sign in as the BOT mailbox account (not your own).", flush=True)
    username = provider.login_interactive(lambda message: print(message, flush=True))
    print(f"mail session established for {username}; cached in the app database")
    return 0


def cmd_whoami(settings: Settings) -> int:
    provider = _provider(settings)
    try:
        asyncio.run(provider.access_token())
    except MailAuthRequired as exc:
        print(f"no session: {exc.message}")
        return 1
    print(f"session ok for {provider.account_username()}")
    return 0


def cmd_logout(settings: Settings) -> int:
    _provider(settings).logout()
    print("mail session removed")
    return 0


def cmd_check(settings: Settings) -> int:
    """Prove the mailbox is reachable: identity and the newest inbox entries."""
    from sc_core.mail.graph import GraphMailClient

    async def run() -> int:
        async with GraphMailClient(_provider(settings)) as graph:
            me = await graph.me()
            address = me.get("mail") or me.get("userPrincipalName")
            print(f"mailbox: {me.get('displayName')} <{address}>")
            page = await graph.inbox_delta(None, page_size=5)
            newest = sorted(
                page.messages, key=lambda m: m.received_at or datetime.min.replace(tzinfo=UTC)
            )[-5:]
            print(f"inbox: {len(page.messages)} message(s) in the first delta page")
            for msg in reversed(newest):
                when = msg.received_at.isoformat(timespec="minutes") if msg.received_at else "?"
                sender = msg.sender.normalized if msg.sender else "?"
                print(f"  {when}  {sender:<40} {(msg.subject or '')[:60]}")
        return 0

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Microsoft Graph mail session")
    parser.add_argument("command", choices=["login", "whoami", "logout", "check"])
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    commands = {"login": cmd_login, "whoami": cmd_whoami, "logout": cmd_logout, "check": cmd_check}
    return commands[args.command](settings)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
