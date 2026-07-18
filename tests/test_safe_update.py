"""Tests for ``safe_update``, extracted from the ``try: await evaluator.update(...) except
Exception: log + continue`` pattern that ``demo.py``'s human-agent-loop and
``evaluation/eval_n1.py``'s N1 eval step each hand-rolled against a live page that can
navigate away or throw mid-update.
"""

import pytest

from navi_bench.base import safe_update


class _FakeEvaluator:
    def __init__(self, error: Exception | None = None):
        self._error = error
        self.calls: list[dict] = []

    async def update(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error


@pytest.mark.asyncio
async def test_safe_update_forwards_kwargs_on_success():
    evaluator = _FakeEvaluator()

    await safe_update(
        evaluator, url="https://example.com", page="page-obj", log_fn=lambda exc: pytest.fail("unexpected")
    )

    assert evaluator.calls == [{"url": "https://example.com", "page": "page-obj"}]


@pytest.mark.asyncio
async def test_safe_update_logs_and_swallows_on_failure():
    evaluator = _FakeEvaluator(error=RuntimeError("boom"))
    logged: list[Exception] = []

    await safe_update(evaluator, url="https://example.com", log_fn=logged.append)

    assert len(logged) == 1
    assert str(logged[0]) == "boom"


@pytest.mark.asyncio
async def test_safe_update_passes_arbitrary_kwargs_through():
    evaluator = _FakeEvaluator()

    await safe_update(evaluator, url="u", page="p", answer_message="done", log_fn=lambda exc: pytest.fail("unexpected"))

    assert evaluator.calls == [{"url": "u", "page": "p", "answer_message": "done"}]
