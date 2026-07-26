"""Shared pytest fixtures and helpers for the navi-bench test suite."""

import asyncio


def run_async(coro):
    """Run an async coroutine to completion and return its result.

    Shared by the domain-matcher characterization tests (apartments, craigslist,
    google_flights), which otherwise each defined an identical local ``_run`` helper.
    """
    return asyncio.run(coro)
