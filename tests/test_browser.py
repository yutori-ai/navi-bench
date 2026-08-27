"""Characterization tests for ``evaluation.browser.get_prepare_page_js`` and
``evaluation.browser.wait_for_page_ready``.

``wait_for_page_ready`` had zero prior direct test coverage even though it hand-rolled the
exact ``try: await page.evaluate(...) except PlaywrightError: log + return default`` pattern
that ``navi_bench.base.safe_evaluate`` exists to centralize (see its docstring and
``test_safe_evaluate.py``) -- it was simply missed when that helper was extracted from
``ResyUrlMatch``/``OpenTableInfoGathering``. These tests pin its retry-on-PlaywrightError,
retry-on-falsy-result, propagate-other-exceptions, and blank/error-page-detection behavior
before that call site is migrated onto ``safe_evaluate`` too.
"""

import asyncio
from pathlib import Path

import pytest
from conftest import FakeEvaluatePage as _FakeReadyPage
from playwright.async_api import Error as PlaywrightError

from evaluation.browser import get_prepare_page_js, wait_for_page_ready


class TestGetPreparePageJs:
    def test_returns_prepare_page_js_contents(self):
        expected = (Path(__file__).parent.parent / "evaluation" / "prepare_page.js").read_text()

        assert get_prepare_page_js() == expected

    def test_result_is_cached(self):
        # get_prepare_page_js is decorated with functools.cache; repeated calls must return
        # the exact same string object, not just an equal one.
        assert get_prepare_page_js() is get_prepare_page_js()


class TestWaitForPageReady:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_first_check_is_ready(self):
        page = _FakeReadyPage([True])

        await wait_for_page_ready(page, sleep_s=0)

        assert page.evaluate_call_count == 1

    @pytest.mark.asyncio
    async def test_retries_after_playwright_error_then_succeeds(self):
        page = _FakeReadyPage([PlaywrightError("boom"), True])

        await wait_for_page_ready(page, sleep_s=0)

        assert page.evaluate_call_count == 2

    @pytest.mark.asyncio
    async def test_retries_while_result_is_falsy_without_erroring(self):
        page = _FakeReadyPage([False, None, True])

        await wait_for_page_ready(page, sleep_s=0)

        assert page.evaluate_call_count == 3

    @pytest.mark.asyncio
    async def test_raises_for_blank_page_once_ready(self):
        page = _FakeReadyPage([True], url="about:blank")

        with pytest.raises(RuntimeError, match="Page is blank or has navigation error"):
            await wait_for_page_ready(page, sleep_s=0)

    @pytest.mark.asyncio
    async def test_raises_for_chrome_error_page_once_ready(self):
        page = _FakeReadyPage([True], url="chrome-error://chromewebdata/")

        with pytest.raises(RuntimeError, match="Page is blank or has navigation error"):
            await wait_for_page_ready(page, sleep_s=0)

    @pytest.mark.asyncio
    async def test_propagates_non_playwright_exceptions_without_retrying(self):
        page = _FakeReadyPage([ValueError("not a playwright error"), True])

        with pytest.raises(ValueError, match="not a playwright error"):
            await wait_for_page_ready(page, sleep_s=0)

        assert page.evaluate_call_count == 1

    @pytest.mark.asyncio
    async def test_honors_initial_sleep_before_first_check(self, monkeypatch):
        page = _FakeReadyPage([True])
        slept: list[float] = []

        real_sleep = asyncio.sleep

        async def _fake_sleep(seconds):
            slept.append(seconds)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
        await wait_for_page_ready(page, sleep_s=1.0)

        assert slept == [1.0]
