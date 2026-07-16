"""Tests for ``safe_evaluate``, extracted from the ``try: await page.evaluate(...) except
PlaywrightError: log + return default`` pattern that ``ResyUrlMatch.update``,
``ResyUrlMatch._extract_availabilities``, and (previously uncaught, now fixed)
``OpenTableInfoGathering.update`` each needed against a live page that can navigate away or
throw a JS error mid-evaluation.
"""

import pytest
from playwright.async_api import Error as PlaywrightError

from navi_bench.base import safe_evaluate


class _FakePage:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.scripts: list[str] = []

    async def evaluate(self, script: str):
        self.scripts.append(script)
        if self._error is not None:
            raise self._error
        return self._result


@pytest.mark.asyncio
async def test_safe_evaluate_returns_evaluated_value_on_success():
    page = _FakePage(result=["a", "b"])

    value = await safe_evaluate(page, "script", default=[], log_message="failed")

    assert value == ["a", "b"]
    assert page.scripts == ["script"]


@pytest.mark.asyncio
async def test_safe_evaluate_returns_default_on_playwright_error(caplog):
    page = _FakePage(error=PlaywrightError("boom"))

    value = await safe_evaluate(page, "script", default=[], log_message="Could not evaluate")

    assert value == []


@pytest.mark.asyncio
async def test_safe_evaluate_logs_with_custom_log_fn():
    page = _FakePage(error=PlaywrightError("boom"))
    logged: list[str] = []

    value = await safe_evaluate(page, "script", default=None, log_message="Could not evaluate", log_fn=logged.append)

    assert value is None
    assert len(logged) == 1
    assert "Could not evaluate" in logged[0]
    assert "boom" in logged[0]


@pytest.mark.asyncio
async def test_safe_evaluate_invokes_on_success_only_when_evaluate_succeeds():
    page = _FakePage(result=True)
    seen: list[bool] = []

    value = await safe_evaluate(page, "script", default=False, log_message="failed", on_success=seen.append)

    assert value is True
    assert seen == [True]


@pytest.mark.asyncio
async def test_safe_evaluate_does_not_invoke_on_success_when_evaluate_fails():
    page = _FakePage(error=PlaywrightError("boom"))
    seen: list[bool] = []

    value = await safe_evaluate(page, "script", default=False, log_message="failed", on_success=seen.append)

    assert value is False
    assert seen == []


@pytest.mark.asyncio
async def test_safe_evaluate_lets_unrelated_exceptions_propagate():
    page = _FakePage(error=ValueError("not a playwright error"))

    with pytest.raises(ValueError):
        await safe_evaluate(page, "script", default=None, log_message="failed")
