#!/usr/bin/env python
"""
Human-in-the-loop demo of Yutori Navi-Bench where:

- The human + Playwright browser act as the "agent loop".
- Each page navigation is treated as an agent step.
- We call evaluator.update(...) on every step.
- At the end, we call evaluator.compute() for the final score.

This demonstrates how you'd integrate Yutori Navi-Bench evaluators into
an agent loop, just with a human standing in for the policy.
"""

import asyncio
from typing import Any, Dict

from datasets import load_dataset
from playwright.async_api import Page, async_playwright

from navi_bench.base import DatasetItem, instantiate


HF_DATASET = "yutori-ai/navi-bench"
HF_SPLIT = "validation"
TASK_ID = "navi_bench/craigslist/craigslist_basic_filters/4"


def load_task(task_id: str) -> Dict[str, Any]:
    """Load a task row by task_id from the dataset."""
    dataset = load_dataset(HF_DATASET, split=HF_SPLIT)
    for row in dataset:
        if row["task_id"] == task_id:
            return row
    raise ValueError(f"Task {task_id} not found in {HF_DATASET}/{HF_SPLIT}")


async def attach_human_agent_loop(page: Page, evaluator) -> None:
    """
    This is the human-agent-loop. Execute the task by navigating the website.
    """

    async def on_navigation():
        try:
            await evaluator.update(url=page.url, page=page)
        except Exception as e:
            print(f"[WARN] evaluator.update(url={page.url!r}, page={page}) failed: {e}")

    page.on("framenavigated", lambda frame: asyncio.create_task(on_navigation()))


async def run_human_session(task_id: str) -> None:
    # Load the task from the dataset and generate the task config
    row = load_task(task_id)

    # Validate the row and generate the task config
    dataset_item = DatasetItem.model_validate(row)
    task_config = dataset_item.generate_task_config()

    # Instantiate the evaluator
    evaluator = instantiate(task_config.eval_config)

    print("\n" + "=" * 80)
    print("TASK")
    print("=" * 80)
    print(f"Task ID: {task_id}")
    print(f"URL:     {task_config.url}")
    print(f"Task:    {task_config.task}")
    print("=" * 80 + "\n")

    input("Press Enter when ready to start the browser...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        await page.goto(task_config.url, timeout=60_000, wait_until="load")

        print(
            "\nBrowser opened.\n"
            "➡ You are now the agent.\n"
            "➡ Follow the instructions in the terminal to complete the task.\n"
            "➡ When done, press ENTER in this terminal (do not close the browser).\n"
        )

        # Reset the evaluator
        await evaluator.reset()
        await evaluator.update(url=task_config.url, page=page)
        await attach_human_agent_loop(page, evaluator)

        # Wait for user to press Enter when task is complete
        await asyncio.to_thread(input, "\nPress Enter when you've completed the task... ")

        # Final update before computing result
        try:
            await evaluator.update(url=page.url, page=page)
        except Exception as e:
            print(f"[WARN] Final evaluator.update(url={page.url!r}, page={page}) failed: {e}")

        # Compute the evaluation result
        print("\nComputing evaluation result...\n")
        result = await evaluator.compute()

        # Now we can close the browser
        await context.close()
        await browser.close()

    # Report the result
    print("=" * 80)
    print("RESULT")
    print("=" * 80)
    print(f"Score: {getattr(result, 'score', None)}")
    if hasattr(result, "reasoning"):
        print(f"Reasoning: {result.reasoning}")
    if hasattr(result, "details"):
        print(f"Details: {result.details}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(run_human_session(TASK_ID))
