#!/usr/bin/env python
"""
Evaluate the Yutori n1 model on the Navi-Bench dataset (https://yutori.com/blog/introducing-navigator).

Authentication: either run `yutori auth login` (credentials are saved to ~/.yutori/config.json)
or set the YUTORI_API_KEY environment variable. If both are present, the env var takes precedence.

Run over one sample:
```
python -m evaluation.eval_n1 \
    --dataset_include_domains 'craigslist' \
    --dataset_max_samples 1
```

Run over all the samples (recommended to specify `BROWSER_CDP_URL` to avoid being blocked by certain websites):
```
BROWSER_CDP_URL=... \
  python -m evaluation.eval_n1
```

To evaluate on other datasets (in the same data schema as Navi-Bench), e.g., Halluminate Westworld, we can:
```
HALLUMINATE_API_KEY=... \
  python -m evaluation.eval_n1 \
    --dataset_name 'Halluminate/westworld'
```

By default, the results will be saved in the `results_n1/` directory and the script will resume from it.
If you want to run from scratch, you can delete the directory or specify a different `--eval_save_dir`.
"""

import asyncio
import base64
import copy
import functools
import io
import json
import os
import sys
import time
import traceback
from datetime import datetime
from os import path as osp
from zoneinfo import ZoneInfo

from PIL import Image

from loguru import logger
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError as OpenAIAuthError,
    InternalServerError,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageFunctionToolCall
from playwright.async_api import Page, Playwright, async_playwright
from pydantic import BaseModel, Field

from evaluation.browser import build_browser, wait_for_page_ready
from evaluation.cli import cli
from evaluation.dataset import build_dataset
from evaluation.recorder import Recorder, log_formatter
from evaluation.stats import BaseTokenUsage, Crashed, TimingStats, show_results, show_timing_summary
from navi_bench.base import BaseMetric, BaseTaskConfig, DatasetItem, instantiate
from yutori import AsyncYutoriClient
from yutori.auth import resolve_api_key
from yutori.n1.payload import trim_images_to_fit

RETRYABLE_API_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


class Config(BaseModel):
    # Yutori n1 model API config
    model_name: str = "n1-experimental"
    # Yutori Navi-Bench dataset config
    dataset_name: str = "yutori-ai/navi-bench"
    dataset_splits: list[str] = Field(default_factory=lambda: ["validation"])
    dataset_revision: str | None = None
    dataset_include_task_ids: list[str] | None = None
    dataset_include_domains: list[str] | None = None
    dataset_max_samples_per_domain: int | None = None
    dataset_max_samples: int | None = None
    # Browser config
    browser_headless: bool = True
    browser_viewport_width: int = 1280
    browser_viewport_height: int = 800
    # Evaluation config
    eval_concurrency: int = 20
    eval_log_name: str = Field(
        default_factory=lambda: (
            f"eval_n1_{datetime.now(tz=ZoneInfo('America/Los_Angeles')).strftime('%Y%m%d_%H%M%S')}.log"
        ),
    )
    eval_save_dir: str = "results_n1"
    eval_max_attempts: int = 3
    eval_max_steps: int = 75
    eval_temperature: float = 0.3
    eval_top_p: float = 1.0
    # Payload management
    eval_max_request_bytes: int = 9_500_000
    eval_keep_recent_screenshots: int = 6


PRICING = {
    "input": 0.75,  # $ per 1M input tokens
    "output": 3.00,  # $ per 1M output tokens
}


class TokenUsage(BaseTokenUsage):
    input_tokens: int = 0
    output_tokens: int = 0

    def __str__(self) -> str:
        return f"TokenUsage(in={self.input_tokens}, out={self.output_tokens})"

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    def calculate_cost(self) -> float:
        input_cost = (self.input_tokens / 1_000_000) * PRICING["input"]
        output_cost = (self.output_tokens / 1_000_000) * PRICING["output"]
        return input_cost + output_cost

    @classmethod
    def show_summary(cls, usages: list["TokenUsage"]) -> None:
        total_usage = sum(usages, start=TokenUsage())
        total_cost = total_usage.calculate_cost()
        avg_cost = total_cost / len(usages) if usages else 0

        logger.info("")
        logger.info("=" * 60)
        logger.info("Token Usage Summary")
        logger.info("=" * 60)
        logger.info(f"  Input tokens:              {total_usage.input_tokens:>12,}")
        logger.info(f"  Output tokens:             {total_usage.output_tokens:>12,}")
        logger.info("-" * 60)
        logger.info(f"  Total cost:                ${total_cost:>11.4f}")
        logger.info(f"  Average cost per task:     ${avg_cost:>11.4f}")
        logger.info(f"  Number of tasks:           {len(usages):>12,}")


async def run_agent(
    config: Config,
    task_config: BaseTaskConfig,
    page: Page,
    evaluator: BaseMetric,
    recorder: Recorder,
    client: AsyncYutoriClient,
) -> tuple[BaseModel, TokenUsage, TimingStats]:

    dt = datetime.fromtimestamp(
        task_config.user_metadata.timestamp,
        tz=ZoneInfo(task_config.user_metadata.timezone),
    )
    user_prompt = f"""{task_config.task}

# User Context
User's location: {task_config.user_metadata.location}
User's timezone: {task_config.user_metadata.timezone}

# Time Context
Current Date: {dt.strftime("%B %-d, %Y")}
Current Time: {dt.strftime("%H:%M:%S %Z")}
Today is: {dt.strftime("%A")}"""

    messages = [{"role": "user", "content": [{"type": "text", "text": user_prompt}]}]
    trimmed_messages: list[dict] | None = None
    tool_call_id_to_observations: dict[str, list[dict]] = {}
    answer_message: str | None = None
    step_idx = 0
    task_usage = TokenUsage()
    task_timing = TimingStats()

    def _denorm(coordinates: list[float], limit: int = 1000) -> tuple[int, int]:
        x, y = coordinates
        x = int(x / limit * config.browser_viewport_width)
        y = int(y / limit * config.browser_viewport_height)
        return x, y

    async def _execute(tool_calls: list[ChatCompletionMessageFunctionToolCall]) -> None:
        for tool_call in tool_calls:
            name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments or "{}")
            tool_call_id_to_observations.setdefault(tool_call.id, [])

            if name == "left_click":
                await page.mouse.click(*_denorm(arguments["coordinates"]))

            elif name == "double_click":
                await page.mouse.click(*_denorm(arguments["coordinates"]), click_count=2)

            elif name == "triple_click":
                await page.mouse.click(*_denorm(arguments["coordinates"]), click_count=3)

            elif name == "right_click":
                await page.mouse.click(*_denorm(arguments["coordinates"]), button="right")

            elif name == "scroll":
                await page.mouse.move(*_denorm(arguments["coordinates"]))
                scroll_amount = arguments["amount"] * 84
                direction = arguments["direction"]
                if direction == "up":
                    await page.mouse.wheel(0, -abs(scroll_amount))
                elif direction == "down":
                    await page.mouse.wheel(0, abs(scroll_amount))
                elif direction == "left":
                    await page.mouse.wheel(-abs(scroll_amount), 0)
                elif direction == "right":
                    await page.mouse.wheel(abs(scroll_amount), 0)

            elif name == "type":
                if arguments.get("clear_before_typing", True):
                    await page.keyboard.press("Control+a")
                    await page.wait_for_timeout(50)
                await page.keyboard.type(arguments["text"])
                await page.wait_for_timeout(50)
                if arguments.get("press_enter_after", True):
                    await page.keyboard.press("Enter")

            elif name == "key_press":
                key_comb = arguments["key_comb"]
                key_comb = "+".join("ControlOrMeta" if k == "Meta" else k for k in key_comb.split("+"))
                await page.keyboard.press(key_comb)

            elif name == "hover":
                await page.mouse.move(*_denorm(arguments["coordinates"]))

            elif name == "drag":
                await page.mouse.move(*_denorm(arguments["start_coordinates"]))
                await page.mouse.down()
                await page.mouse.move(*_denorm(arguments["coordinates"]))
                await page.mouse.up()

            elif name == "wait":
                await asyncio.sleep(5)

            elif name == "refresh":
                await page.reload()

            elif name == "go_back":
                await page.go_back()

            elif name == "goto_url":
                await page.goto(arguments["url"])

            else:
                raise RuntimeError(f"Unknown action type: {name}")

    async def _fail(
        reason: str,
        exception: Exception | None = None,
        do_evaluator_update: bool = False,
    ) -> tuple[BaseModel, TokenUsage, TimingStats]:
        if do_evaluator_update:
            try:
                await evaluator.update(url=page.url, page=page, answer_message=answer_message)
            except Exception:
                logger.opt(exception=True).warning(f"[{step_idx}] Failed to update evaluator: {page.url}")

        result = await evaluator.compute()
        await recorder.save_messages(messages)
        await recorder.save_html(messages, result)
        if result.score > 0:
            logger.warning(f"[{step_idx}] {reason}. Returning with the evaluator's score: {result.score}")
            await recorder.save_result(result)
            await recorder.save_usage(task_usage)
            await recorder.save_timing(task_timing)
            return result, task_usage, task_timing
        else:
            raise RuntimeError(reason) from exception

    async def _predict(
        messages: list[dict],
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> ChatCompletionMessage:
        # Keep a separate trimmed copy so in-place image stripping does not
        # destroy screenshots in the original history (needed for HTML vis).
        nonlocal trimmed_messages
        if trimmed_messages is None:
            trimmed_messages = copy.deepcopy(messages)
        else:
            trimmed_messages.extend(copy.deepcopy(messages[len(trimmed_messages):]))
        size_bytes, removed = trim_images_to_fit(
            trimmed_messages,
            max_bytes=config.eval_max_request_bytes,
            keep_recent=config.eval_keep_recent_screenshots,
        )
        if removed:
            size_mb = size_bytes / (1024 * 1024)
            logger.info(f"[{step_idx}] Trimmed {removed} old screenshot(s); payload ~{size_mb:.2f} MB")

        delay = initial_delay
        for attempt in range(1, max_attempts + 1):
            content = None
            try:
                kwargs = {}
                if config.eval_temperature is not None:
                    kwargs["temperature"] = config.eval_temperature
                if config.eval_top_p is not None:
                    kwargs["top_p"] = config.eval_top_p
                start_time = time.perf_counter()
                response = await asyncio.wait_for(
                    client.chat.completions.create(model=config.model_name, messages=trimmed_messages, **kwargs),
                    timeout=120,
                )
                logger.debug(f"[{step_idx}] {response=}")
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                task_timing.add_call(elapsed_ms)

                usage = response.usage
                step_usage = TokenUsage(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)
                step_cost = step_usage.calculate_cost()

                nonlocal task_usage
                task_usage = task_usage + step_usage

                logger.debug(f"[{step_idx}] time={elapsed_ms:.0f}ms, cost=${step_cost:.4f}, usage={step_usage}")

                return response.choices[0].message

            except OpenAIAuthError:
                raise
            except RETRYABLE_API_ERRORS:
                if attempt == max_attempts:
                    logger.opt(exception=True).error(
                        f"[{step_idx}] Failed to get valid response: {content=}. No more attempts."
                    )
                    raise
                logger.opt(exception=True).warning(
                    f"[{step_idx}] Failed to get valid response: {content=}. Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
            except APIStatusError:
                raise
            except Exception:
                if attempt == max_attempts:
                    logger.opt(exception=True).error(
                        f"[{step_idx}] Failed to get valid response: {content=}. No more attempts."
                    )
                    raise
                logger.opt(exception=True).warning(
                    f"[{step_idx}] Failed to get valid response: {content=}. Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    while step_idx < config.eval_max_steps:
        step_idx += 1

        try:
            await asyncio.wait_for(wait_for_page_ready(page, step_idx), timeout=30)
        except asyncio.TimeoutError:
            return await _fail(f"Timeout waiting for page ready within 30 seconds: {page.url}")
        except Exception as e:
            return await _fail(f"Failed to wait for page ready: {page.url}", e)

        try:
            screenshot_jpeg = await page.screenshot(full_page=False, type="jpeg", quality=75)
            img = Image.open(io.BytesIO(screenshot_jpeg))
            webp_buf = io.BytesIO()
            img.save(webp_buf, format="WEBP", quality=90)
            screenshot = webp_buf.getvalue()
        except Exception as e:
            return await _fail(f"Failed to take screenshot: {page.url}", e)

        screenshot_base64 = base64.b64encode(screenshot).decode("utf-8")
        screenshot_block = {
            "type": "image_url",
            "image_url": {"url": f"data:image/webp;base64,{screenshot_base64}", "detail": "high"},
        }

        # Append tool observations and screenshot to messages
        for tool_call_id, observations in tool_call_id_to_observations.items():
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": observations})
        messages[-1]["content"].append(screenshot_block)

        tool_call_id_to_observations = {}

        try:
            await evaluator.update(url=page.url, page=page, answer_message=answer_message)
        except Exception:
            logger.opt(exception=True).warning(f"[{step_idx}] Failed to update evaluator: {page.url}")

        try:
            message = await _predict(messages)
            logger.info(f"[{step_idx}] {message}")
        except OpenAIAuthError:
            raise
        except RETRYABLE_API_ERRORS as e:
            return await _fail(f"Failed to get valid response: {e}", e, do_evaluator_update=True)
        except APIStatusError:
            raise
        except Exception as e:
            return await _fail(f"Failed to get valid response: {e}", e, do_evaluator_update=True)

        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            answer_message = message.content
            break

        try:
            await _execute(message.tool_calls)
        except Exception as e:
            return await _fail(f"Failed to execute the tool calls: {message.tool_calls}", e, do_evaluator_update=True)

    try:
        await evaluator.update(url=page.url, page=page, answer_message=answer_message)
    except Exception:
        logger.opt(exception=True).warning(f"[{step_idx}] Failed to update evaluator: {page.url}")

    result = await evaluator.compute()

    await recorder.save_messages(messages)
    await recorder.save_result(result)
    await recorder.save_usage(task_usage)
    await recorder.save_timing(task_timing)
    await recorder.save_html(messages, result)

    task_cost = task_usage.calculate_cost()
    logger.info(f"Task usage: cost=${task_cost:.4f}, usage={task_usage}")
    logger.info(
        f"Task timing: calls={task_timing.call_count}, total={task_timing.total_time_ms:.0f}ms, "
        f"avg={task_timing.avg_time_ms:.0f}ms, min={task_timing.min_time_ms:.0f}ms, max={task_timing.max_time_ms:.0f}ms"
    )

    return result, task_usage, task_timing


async def run_task(
    config: Config, item: DatasetItem, playwright: Playwright, recorder: Recorder, client: AsyncYutoriClient
) -> tuple[BaseModel | Crashed, TokenUsage, TimingStats]:
    for attempt in range(1, config.eval_max_attempts + 1):
        with logger.contextualize(attempt=f"attempt {attempt}/{config.eval_max_attempts}"):
            try:
                task_config = item.generate_task_config()
                logger.info(task_config)
                evaluator = instantiate(task_config.eval_config)
                async with build_browser(config, task_config, playwright) as (_, _, page):
                    return await run_agent(config, task_config, page, evaluator, recorder, client)
            except OpenAIAuthError:
                raise
            except APIStatusError as e:
                if isinstance(e, (RateLimitError, InternalServerError)):
                    if attempt == config.eval_max_attempts:
                        logger.opt(exception=True).error(f"Failed to run task: {e}. No more attempts.")
                        return (
                            Crashed(score=0.0, exception=str(e), traceback=traceback.format_exc()),
                            TokenUsage(),
                            TimingStats(),
                        )
                    logger.opt(exception=True).warning(f"Failed to run task: {e}. Will retry.")
                    continue
                raise
            except Exception as e:
                if attempt == config.eval_max_attempts:
                    logger.opt(exception=True).error(f"Failed to run task: {e}. No more attempts.")
                    return (
                        Crashed(score=0.0, exception=str(e), traceback=traceback.format_exc()),
                        TokenUsage(),
                        TimingStats(),
                    )
                logger.opt(exception=True).warning(f"Failed to run task: {e}. Will retry.")


@cli
async def main(config: Config) -> None:
    os.makedirs(config.eval_save_dir, exist_ok=True)

    logger.remove()
    logger.level("DEBUG", color="<fg #808080>")
    logger.add(sys.stdout, format=functools.partial(log_formatter, colorize=True))
    logger.add(
        osp.join(config.eval_save_dir, config.eval_log_name), format=functools.partial(log_formatter, colorize=False)
    )
    logger.info(f"{config=}")

    api_key = resolve_api_key()
    if not api_key:
        raise ValueError("No Yutori API key found. Set YUTORI_API_KEY env var or run: yutori auth login")

    dataset = await build_dataset(config)

    semaphore = asyncio.Semaphore(config.eval_concurrency)
    async with async_playwright() as playwright, AsyncYutoriClient(api_key=api_key) as client:

        async def _eval(
            item: DatasetItem,
        ) -> tuple[BaseModel | Crashed, TokenUsage, TimingStats]:
            async with semaphore:
                with logger.contextualize(task_id=item.task_id):
                    recorder = Recorder(config.eval_save_dir, item.task_id)
                    result = await recorder.load_result()
                    if result is not None:
                        usage = await recorder.load_usage(TokenUsage) or TokenUsage()
                        timing = await recorder.load_timing() or TimingStats()
                        logger.info("Already evaluated. Returning the existing result directly.")
                        return result, usage, timing
                    with recorder.logging():
                        try:
                            return await run_task(config, item, playwright, recorder, client)
                        except OpenAIAuthError:
                            raise
                        except Exception as e:
                            logger.opt(exception=True).error(
                                f"Unhandled exception escaped run_task: {e}. "
                                "Marking this task as crashed and continuing."
                            )
                            return (
                                Crashed(score=0.0, exception=str(e), traceback=traceback.format_exc()),
                                TokenUsage(),
                                TimingStats(),
                            )

        eval_tasks = [asyncio.create_task(_eval(item), name=f"eval:{item.task_id}") for item in dataset]
        try:
            results_with_stats = await asyncio.gather(*eval_tasks)
        except OpenAIAuthError:
            # Fail fast on auth errors, but cancel/await siblings to avoid orphaned task warnings.
            for task in eval_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*eval_tasks, return_exceptions=True)
            raise

    results = [r for r, _, _ in results_with_stats]
    usages = [u for _, u, _ in results_with_stats]
    timings = [t for _, _, t in results_with_stats]

    TokenUsage.show_summary(usages)
    show_timing_summary(timings)
    show_results(dataset, results)


if __name__ == "__main__":
    main()
