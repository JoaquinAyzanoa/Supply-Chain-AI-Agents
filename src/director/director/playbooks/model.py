"""The playbook files as typed models, validated on load."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from director.playbooks.conditions import CONDITIONS
from sc_core.schema.base import StrictModel

PLAYBOOKS_DIR = Path(__file__).resolve().parent

StepKind = Literal["agent", "wait", "action"]
Fallback = Literal["escalate", "skip"]


class Step(StrictModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = ""
    when: str = "always"  # a condition name; the step is skipped when it does not hold
    # agent step
    agent: str | None = None
    task: str | None = None
    notes: str | None = None
    fallback: Fallback = "escalate"  # when the agent is not deployed yet
    # wait step
    wait_days: int | None = Field(default=None, ge=0)
    until: str | None = None  # a condition that ends the wait early
    # action step
    action: Literal["close", "escalate"] | None = None
    reason: str | None = None

    @property
    def kind(self) -> StepKind:
        if self.agent:
            return "agent"
        if self.wait_days is not None:
            return "wait"
        return "action"

    @model_validator(mode="after")
    def _one_kind(self) -> Step:
        kinds = sum([bool(self.agent), self.wait_days is not None, bool(self.action)])
        if kinds != 1:
            raise ValueError(f"step {self.id!r} must be one of agent, wait or action")
        if self.agent and not self.task:
            raise ValueError(f"step {self.id!r} names an agent but no task")
        for name in (self.when, self.until):
            if name is not None and name not in CONDITIONS:
                raise ValueError(f"step {self.id!r}: unknown condition {name!r}")
        return self

    def describe(self) -> str:
        if self.label:
            return self.label
        if self.kind == "agent":
            return f"{self.agent}: {self.task}"
        if self.kind == "wait":
            return f"wait {self.wait_days} day(s)" + (
                f" or until {self.until}" if self.until else ""
            )
        return str(self.action)


class Playbook(StrictModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    description: str = ""
    trigger: str = "manual"  # who starts it: po_late | rfq_silent | discrepancy | manual
    case_kind: str = "eta"
    # when this condition holds, the run ends and the case closes, whatever the step
    done_when: str | None = None
    done_reason: str = ""
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_steps(self) -> Playbook:
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError(f"playbook {self.name!r}: duplicate step ids")
        if self.done_when is not None and self.done_when not in CONDITIONS:
            raise ValueError(f"playbook {self.name!r}: unknown condition {self.done_when!r}")
        return self

    def step(self, index: int) -> Step | None:
        return self.steps[index] if 0 <= index < len(self.steps) else None

    def index_of(self, step_id: str) -> int:
        return next(i for i, s in enumerate(self.steps) if s.id == step_id)


def load_playbooks(directory: Path | None = None) -> dict[str, Playbook]:
    """Every ``*.yml`` in the directory, by name; a broken file fails at startup."""
    out: dict[str, Playbook] = {}
    for path in sorted((directory or PLAYBOOKS_DIR).glob("*.yml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        playbook = Playbook.model_validate(raw)
        out[playbook.name] = playbook
    return out
