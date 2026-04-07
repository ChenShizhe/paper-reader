"""Stable dataclass contracts for subagent orchestration in the paper-reader skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SubagentInput:
    """Input contract passed to a paper-reader subagent for one section pass."""

    cite_key: str
    section_type: str
    segment_ids: list[str]
    layer_a_context: str
    layer_b_meta_notes: list[str]
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentOutput:
    """Output contract returned by a paper-reader subagent after one section pass."""

    cite_key: str
    section_type: str
    status: str
    notes_written: list[str]
    catalog_updates: dict[str, Any]
    flags: list[str]
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchRecord:
    """Audit record for a single subagent dispatch within an orchestration round."""

    subagent_id: str
    input: SubagentInput
    output: Optional[SubagentOutput]
    round_number: int
    error: Optional[str] = None
