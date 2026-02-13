import asyncio
import functools
import os
from contextlib import asynccontextmanager
from os import path as osp
from typing import AsyncIterator

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, Playwright

from navi_bench.base import BaseTaskConfig


LOCATION_COORDS = {
    "Boston, MA, United States": (42.3601, -71.0589),
    "New York, NY, United States": (40.7128, -74.0060),
    "San Francisco, CA, United States": (37.7749, -122.4194),
    "Los Angeles, CA, United States": (34.0522, -118.2437),
    "Vancouver, BC, Canada": (49.2827, -123.1207),
}

BLOCKED_URL_KEYWORDS = (
    "www.facebook.com/tr",
    "connect.facebook.net",
    "googletagmanager.com",
    "google-analytics.com",
)


@functools.cache
def get_prepare_page_js() -> str:
    with open(osp.join(osp.dirname(__file__), "prepare_page.js"), "r") as f:
        return f.read()


async def wait_for_page_ready(page: Page, step_idx: int = -1, sleep_s: float = 1.0) -> None:
    """Wait for a page to finish loading, then verify it's not an error page."""
    await asyncio.sleep(sleep_s)

    while True:
        try:
            is_ready = await page.evaluate(get_prepare_page_js())
            if is_ready:
                break
        except Exception as e:
            prefix = f"[{step_idx}] " if step_idx >= 0 else ""
            logger.warning(f"{prefix}Failed to wait for page ready: {e}. Continue waiting")
        await asyncio.sleep(sleep_s)

    if page.url == "about:blank" or page.url.startswith("chrome-error://") or page.url.startswith("about:neterror"):
        raise RuntimeError("Page is blank or has navigation error")


@asynccontextmanager
async def build_browser(
    config, task_config: BaseTaskConfig, playwright: Playwright
) -> AsyncIterator[tuple[Browser, BrowserContext, Page]]:
    """Create a browser, context, and page for evaluation.

    Config must have: browser_headless, browser_viewport_width, browser_viewport_height.
    """
    browser = None
    context = None

    try:
        need_to_set_location = "opentable.com" in task_config.url or "resy.com" in task_config.url

        use_local_browser = "apartments.com" not in task_config.url and "resy.com" not in task_config.url
        if not use_local_browser and not os.getenv("BROWSER_CDP_URL"):
            logger.warning(
                f"BROWSER_CDP_URL is not set. Falling back to local browser for: {task_config.url}. "
                "However, this may be blocked by certain websites, leading to crashes. "
                "After the current run, you may try running the eval script again with `--eval_concurrency 2` "
                "to redo the crashed tasks."
            )
            use_local_browser = True

        if use_local_browser:
            context_kwargs = {
                "viewport": {"width": config.browser_viewport_width, "height": config.browser_viewport_height},
                "timezone_id": task_config.user_metadata.timezone,
            }
            if need_to_set_location:
                if coords := LOCATION_COORDS.get(task_config.user_metadata.location):
                    context_kwargs["geolocation"] = {"latitude": coords[0], "longitude": coords[1]}
                    context_kwargs["permissions"] = ["geolocation"]
            browser = await playwright.webkit.launch(headless=config.browser_headless)
            context = await browser.new_context(**context_kwargs)
        else:
            browser = await playwright.chromium.connect_over_cdp(os.getenv("BROWSER_CDP_URL"))
            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = await browser.new_context(
                    viewport={"width": config.browser_viewport_width, "height": config.browser_viewport_height}
                )

        async def handle_dialog(dialog):
            await dialog.accept()

        context.on("dialog", handle_dialog)

        async def route_handler(route, request):
            if any(k in request.url for k in BLOCKED_URL_KEYWORDS):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_handler)

        if context.pages:
            page = context.pages[0]
        else:
            page = await context.new_page()
        if page.viewport_size != {"width": config.browser_viewport_width, "height": config.browser_viewport_height}:
            await page.set_viewport_size(
                {"width": config.browser_viewport_width, "height": config.browser_viewport_height}
            )

        if need_to_set_location and not use_local_browser:
            try:
                if coords := LOCATION_COORDS.get(task_config.user_metadata.location):
                    cdp_session = await context.new_cdp_session(page)
                    await cdp_session.send(
                        "Proxy.setLocation", {"lat": coords[0], "lon": coords[1], "distance": 100, "strict": False}
                    )
                    logger.info(f"Set location for CDP session: {task_config.user_metadata.location}")
            except Exception:
                logger.opt(exception=True).warning(
                    f"Failed to set location for CDP session: {task_config.user_metadata.location}"
                )

        await page.goto(task_config.url, wait_until="load")
        yield browser, context, page

    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                logger.opt(exception=True).warning("Failed to close browser context")

        if browser is not None:
            try:
                await browser.close()
            except Exception:
                logger.opt(exception=True).warning("Failed to close browser")
