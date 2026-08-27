"""Characterization tests for ``evaluation.eval_n1``'s fatal-vs-retryable API-error rule.

``eval_n1.py`` had three hand-rolled ``except`` ladders implementing one shared rule -- "an
``APIStatusError`` that is not in ``RETRYABLE_API_ERRORS`` must propagate; everything else is
non-fatal" -- spelled three different ways, one of which (``run_task``) used the narrower
literal tuple ``(RateLimitError, InternalServerError)`` instead of ``RETRYABLE_API_ERRORS``.
The ladders were order-dependent in a non-obvious way (``RateLimitError`` and
``InternalServerError`` are themselves ``APIStatusError`` subclasses, so an ``except
APIStatusError: raise`` clause placed before the retryable clause would silently turn every
rate limit into a hard failure), which is why an earlier audit flagged them as unsafe to
merge mechanically.

These tests pin the rule itself, pin that the legacy ``run_task`` spelling agrees with it
across the full OpenAI exception vocabulary, and exercise ``run_task``'s real retry loop --
which had zero prior test coverage -- for both the propagate and retry-then-crash outcomes.
"""

import asyncio

import httpx
import pytest
from conftest import run_async as _run
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from evaluation.eval_n1 import (
    RETRYABLE_API_ERRORS,
    Config,
    TimingStats,
    TokenUsage,
    _is_fatal_api_error,
    _split_results_with_stats,
    run_task,
)
from evaluation.stats import Crashed


_REQUEST = httpx.Request("POST", "https://api.example.invalid/v1/chat/completions")


def _status_error(cls, status_code: int):
    return cls(cls.__name__, response=httpx.Response(status_code, request=_REQUEST), body=None)


# Non-retryable ``APIStatusError`` subclasses: retrying an identically-bad request cannot help.
FATAL_EXCEPTIONS = [
    _status_error(AuthenticationError, 401),
    _status_error(PermissionDeniedError, 403),
    _status_error(BadRequestError, 400),
    _status_error(NotFoundError, 404),
    _status_error(ConflictError, 409),
    _status_error(UnprocessableEntityError, 422),
    _status_error(APIStatusError, 418),
]

# Transport failures, rate limits, upstream 5xx, and anything that is not an OpenAI status error.
NON_FATAL_EXCEPTIONS = [
    _status_error(RateLimitError, 429),
    _status_error(InternalServerError, 500),
    APIConnectionError(request=_REQUEST),
    APITimeoutError(request=_REQUEST),
    APIError("api error", _REQUEST, body=None),
    OpenAIError("generic openai error"),
    ValueError("plain value error"),
    RuntimeError("plain runtime error"),
    asyncio.TimeoutError(),
    Exception("bare exception"),
]


def _legacy_run_task_ladder(exc: BaseException) -> str:
    """The pre-refactor ``run_task`` ``except`` ladder, transcribed verbatim.

    Note it tested the narrower literal ``(RateLimitError, InternalServerError)`` rather than
    ``RETRYABLE_API_ERRORS``; ``APIConnectionError``/``APITimeoutError`` reached the same
    "handle" outcome via the trailing ``except Exception`` clause instead.
    """
    try:
        raise exc
    except AuthenticationError:
        return "raise"
    except APIStatusError as e:
        if isinstance(e, (RateLimitError, InternalServerError)):
            return "handle"
        return "raise"
    except Exception:
        return "handle"


def _new_ladder(exc: BaseException) -> str:
    try:
        raise exc
    except Exception as e:
        return "raise" if _is_fatal_api_error(e) else "handle"


class TestIsFatalApiError:
    @pytest.mark.parametrize("exc", FATAL_EXCEPTIONS, ids=lambda e: type(e).__name__)
    def test_non_retryable_status_errors_are_fatal(self, exc):
        assert _is_fatal_api_error(exc) is True

    @pytest.mark.parametrize("exc", NON_FATAL_EXCEPTIONS, ids=lambda e: type(e).__name__)
    def test_retryable_and_non_status_errors_are_not_fatal(self, exc):
        assert _is_fatal_api_error(exc) is False

    def test_every_retryable_api_error_class_is_non_fatal(self):
        """Pins the ordering hazard: ``RateLimitError``/``InternalServerError`` are
        ``APIStatusError`` subclasses, so a naive ``isinstance(exc, APIStatusError)`` check
        placed before the retryable check would misclassify them as fatal."""
        for cls in RETRYABLE_API_ERRORS:
            exc = cls.__new__(cls)
            assert issubclass(cls, Exception)
            assert _is_fatal_api_error(exc) is False, f"{cls.__name__} must stay retryable"

    def test_two_retryable_classes_are_status_error_subclasses(self):
        """Guards the premise of the test above -- if OpenAI ever restructured its hierarchy
        so none of the retryable errors were status errors, the ordering note would be stale."""
        assert issubclass(RateLimitError, APIStatusError)
        assert issubclass(InternalServerError, APIStatusError)
        assert not issubclass(APIConnectionError, APIStatusError)

    @pytest.mark.parametrize("exc", FATAL_EXCEPTIONS + NON_FATAL_EXCEPTIONS, ids=lambda e: type(e).__name__)
    def test_agrees_with_legacy_run_task_ladder(self, exc):
        assert _new_ladder(exc) == _legacy_run_task_ladder(exc)


class _RaisingItem:
    """Minimal ``DatasetItem`` stand-in whose ``generate_task_config`` always raises.

    ``run_task`` touches nothing else on the item before that call, so this exercises its
    exception ladder without a browser, recorder, or API client.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    def generate_task_config(self):
        self.calls += 1
        raise self._exc


def _run_task(item: _RaisingItem, max_attempts: int = 3):
    config = Config(eval_max_attempts=max_attempts)
    return _run(run_task(config, item, None, None, None))


class TestRunTaskErrorHandling:
    @pytest.mark.parametrize("exc", FATAL_EXCEPTIONS, ids=lambda e: type(e).__name__)
    def test_propagates_fatal_error_without_retrying(self, exc):
        item = _RaisingItem(exc)
        with pytest.raises(type(exc)):
            _run_task(item)
        assert item.calls == 1

    @pytest.mark.parametrize("exc", NON_FATAL_EXCEPTIONS, ids=lambda e: type(e).__name__)
    def test_retries_non_fatal_error_then_returns_crashed(self, exc):
        item = _RaisingItem(exc)
        result, usage, timing = _run_task(item)

        assert item.calls == 3
        assert isinstance(result, Crashed)
        assert result.score == 0.0
        assert result.exception == str(exc)
        assert type(exc).__name__ in result.traceback
        assert usage == TokenUsage()
        assert timing == TimingStats()

    def test_rate_limit_is_retried_not_propagated(self):
        """The specific regression the predicate's check order protects: ``RateLimitError``
        is an ``APIStatusError`` subclass but must be retried, not re-raised."""
        item = _RaisingItem(_status_error(RateLimitError, 429))
        result, _, _ = _run_task(item)
        assert item.calls == 3
        assert isinstance(result, Crashed)

    def test_attempt_count_follows_config(self):
        item = _RaisingItem(ValueError("boom"))
        result, _, _ = _run_task(item, max_attempts=1)
        assert item.calls == 1
        assert isinstance(result, Crashed)


class TestSplitResultsWithStats:
    """``main()`` gathers ``(result, usage, timing)`` tuples and unzips them into three parallel
    lists before handing them to ``TokenUsage.show_summary``/``show_timing_summary``/
    ``show_results``. ``main()`` itself has no unit coverage (it drives a real browser/API
    client), so this pins the pure unzip logic directly.
    """

    def test_empty_input_returns_three_empty_lists(self):
        assert _split_results_with_stats([]) == ([], [], [])

    def test_splits_and_preserves_order(self):
        crashed = Crashed(score=0.0, exception="boom", traceback="")
        results_with_stats = [
            (crashed, TokenUsage(input_tokens=1), TimingStats(times_ms=[10])),
            (Crashed(score=1.0), TokenUsage(input_tokens=2), TimingStats(times_ms=[20])),
        ]

        results, usages, timings = _split_results_with_stats(results_with_stats)

        assert results == [crashed, Crashed(score=1.0)]
        assert usages == [TokenUsage(input_tokens=1), TokenUsage(input_tokens=2)]
        assert timings == [TimingStats(times_ms=[10]), TimingStats(times_ms=[20])]

    def test_single_element_input(self):
        crashed = Crashed(score=0.5)
        usage = TokenUsage(input_tokens=3)
        timing = TimingStats(times_ms=[5])

        results, usages, timings = _split_results_with_stats([(crashed, usage, timing)])

        assert results == [crashed]
        assert usages == [usage]
        assert timings == [timing]
