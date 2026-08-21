"""节点拓扑声明 — 换列表即可换驱动，不改节点实现。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SkipWhen(StrEnum):
    NON_WEB = "non_web"
    GATE_FAIL = "gate_fail"
    GATE_UNCERTAIN = "gate_uncertain"


@dataclass(frozen=True)
class NodeSpec:
    key: str
    index: int
    requires: tuple[str, ...]
    produces: str
    skip_when: frozenset[SkipWhen] = frozenset()


DEFAULT_PIPELINE: tuple[NodeSpec, ...] = (
    NodeSpec(key="source", index=0, requires=(), produces="source"),
    NodeSpec(key="profile", index=1, requires=("source",), produces="profile"),
    NodeSpec(
        key="env_ready",
        index=2,
        requires=("source", "profile"),
        produces="env_ready",
        skip_when=frozenset({SkipWhen.NON_WEB}),
    ),
    NodeSpec(
        key="audit",
        index=3,
        requires=("source", "profile"),
        produces="audit",
        skip_when=frozenset({SkipWhen.NON_WEB}),
    ),
    NodeSpec(
        key="reproduce",
        index=4,
        requires=("source", "env_ready", "audit"),
        produces="reproduce",
        skip_when=frozenset({SkipWhen.NON_WEB, SkipWhen.GATE_FAIL, SkipWhen.GATE_UNCERTAIN}),
    ),
    NodeSpec(
        key="report",
        index=5,
        requires=("profile", "env_ready", "audit", "reproduce"),
        produces="report",
        skip_when=frozenset({SkipWhen.NON_WEB}),
    ),
)

NODE_BY_KEY: dict[str, NodeSpec] = {spec.key: spec for spec in DEFAULT_PIPELINE}


def node_by_key(key: str) -> NodeSpec:
    try:
        return NODE_BY_KEY[key]
    except KeyError as e:
        raise KeyError(f"未知节点: {key}") from e
