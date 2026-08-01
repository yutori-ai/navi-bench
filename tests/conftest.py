"""Shared pytest fixtures and helpers for the navi-bench test suite."""

import asyncio


def run_async(coro):
    """Run an async coroutine to completion and return its result.

    Shared across the test suite so individual test modules don't each redefine
    an identical local ``_run`` helper.
    """
    return asyncio.run(coro)


class FakeEvaluatePage:
    """Fake page whose ``evaluate()`` pops one scripted result (or raises it, if the
    scripted value is an exception) per call, from a pre-scripted sequence.

    Shared by ``test_browser.py`` (as ``_FakeReadyPage``) and ``test_resy_url_match.py``
    (as ``_FakePage``), which each defined a byte-for-byte identical class under a
    different name to drive code that repeatedly calls ``page.evaluate(...)`` without a
    real browser -- ``wait_for_page_ready``'s retry loop and ``ResyUrlMatch.update``'s two
    sequential evaluate calls, respectively.
    """

    def __init__(self, results: list, url: str = "https://example.com/ready"):
        self._results = list(results)
        self.url = url
        self.evaluate_call_count = 0

    async def evaluate(self, script: str):
        self.evaluate_call_count += 1
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result
