"""The chat test doubles: scripted, failing, and cassette record/replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from sc_core.llm.client import ChatResult, Usage, user
from sc_core.llm.testing import (
    FAKE_SPEC,
    ChatCassette,
    FailingChatClient,
    RecordingChatClient,
    ReplayChatClient,
    ScriptedChatClient,
)
from sc_core.shared.errors import ExternalServiceError


async def test_scripted_records_calls_and_runs_out() -> None:
    client = ScriptedChatClient(["uno", "dos"])
    assert (await client.complete([user("a")])).text == "uno"
    assert (await client.complete([user("b")], name="x")).text == "dos"
    assert client.calls[1].options["name"] == "x" and client.last_prompt_text() == "b"
    with pytest.raises(AssertionError):
        await client.complete([user("c")])


async def test_failing_client() -> None:
    with pytest.raises(ExternalServiceError):
        await FailingChatClient().complete([user("a")])


async def test_record_then_replay(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    inner = ScriptedChatClient(["first", "second"])
    recorder = RecordingChatClient(inner, ChatCassette(path=path))
    assert (await recorder.complete([user("q")], temperature=0)).text == "first"
    assert (
        await recorder.complete([user("q")], temperature=0)
    ).text == "second"  # same key, ordered
    assert path.exists()

    replay = ReplayChatClient(ChatCassette.load(path), FAKE_SPEC)
    assert (await replay.complete([user("q")], temperature=0)).text == "first"
    assert (await replay.complete([user("q")], temperature=0)).text == "second"
    assert (
        await replay.complete([user("q")], temperature=0)
    ).text == "second"  # exhausted: last reused
    with pytest.raises(AssertionError, match="llm-record"):
        await replay.complete([user("different")], temperature=0)


def test_missing_cassette_message(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="llm-record"):
        ChatCassette.load(tmp_path / "nope.json")


def test_chat_result_roundtrip() -> None:
    result = ChatResult(
        text="t",
        messages=[],
        finish_reason=None,
        model="m",
        usage=Usage(),
        cost_usd=0.0,
        duration_ms=0.0,
    )
    assert ChatResult.model_validate(result.model_dump()) == result
