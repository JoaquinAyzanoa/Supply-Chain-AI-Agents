"""The checkpointer's setup survives losing the start-up race to another agent."""

from __future__ import annotations

from typing import cast

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import errors

from sc_core.graph import checkpointer


class RacingSaver:
    """Fails like a saver whose migration was applied by another agent a moment earlier."""

    def __init__(self, losses: int) -> None:
        self.losses = losses
        self.calls = 0

    async def setup(self) -> None:
        self.calls += 1
        if self.calls <= self.losses:
            raise errors.UniqueViolation("checkpoint_migrations_pkey")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instantly(_seconds: float) -> None:
        return None

    monkeypatch.setattr(checkpointer.asyncio, "sleep", instantly)


async def test_setup_tries_again_when_another_agent_applied_the_migration() -> None:
    saver = RacingSaver(losses=2)
    await checkpointer._setup(cast(AsyncPostgresSaver, saver))
    assert saver.calls == 3


async def test_setup_gives_up_when_the_failure_is_not_a_race() -> None:
    saver = RacingSaver(losses=checkpointer.SETUP_ATTEMPTS)
    with pytest.raises(errors.UniqueViolation):
        await checkpointer._setup(cast(AsyncPostgresSaver, saver))
    assert saver.calls == checkpointer.SETUP_ATTEMPTS
