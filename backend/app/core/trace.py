"""Opt-in, request-scoped tracing of model calls and named spans.

The AI Call Simulator wraps one agent turn in `collect()` to show exactly
which model calls ran, what they returned and how long each took, plus the
ticket payload the orchestrator built -- without changing any function
signature on the production voice path.

When no trace is active (every non-simulator request), `record_llm_call` and
`span` do nothing beyond a context-variable lookup.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LLMCall:
    kind: str  # "structured" | "text"
    schema_name: str | None
    started_ms: float
    duration_ms: float
    system: str
    user: str
    output: Any
    error: str | None
    attempts: int


@dataclass
class Span:
    name: str
    started_ms: float
    duration_ms: float
    data: dict[str, Any] | None = None


@dataclass
class Trace:
    origin: float = field(default_factory=time.perf_counter)
    llm_calls: list[LLMCall] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)

    def offset_ms(self, at: float) -> float:
        return round((at - self.origin) * 1000, 2)

    def llm_calls_json(self) -> list[dict[str, Any]]:
        return [asdict(call) for call in self.llm_calls]

    def span_named(self, name: str) -> Span | None:
        return next((s for s in self.spans if s.name == name), None)


_current: ContextVar[Trace | None] = ContextVar("hfmg_trace", default=None)


def active() -> Trace | None:
    return _current.get()


@contextmanager
def collect() -> Iterator[Trace]:
    """Activate a trace for the enclosed block."""
    trace = Trace()
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)


def record_llm_call(
    *,
    kind: str,
    schema_name: str | None,
    started: float,
    system: str,
    user: str,
    output: Any,
    error: str | None,
    attempts: int,
) -> None:
    trace = _current.get()
    if trace is None:
        return
    trace.llm_calls.append(
        LLMCall(
            kind=kind,
            schema_name=schema_name,
            started_ms=trace.offset_ms(started),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            system=system,
            user=user,
            output=output,
            error=error,
            attempts=attempts,
        )
    )


@contextmanager
def span(name: str, data: dict[str, Any] | None = None) -> Iterator[None]:
    """Time a named block into the active trace, if there is one."""
    trace = _current.get()
    if trace is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        trace.spans.append(
            Span(
                name=name,
                started_ms=trace.offset_ms(started),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                data=data,
            )
        )
