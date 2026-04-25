import functools
import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from os import path as osp
from typing import TypeVar

import aiofiles
from loguru import logger
from pydantic import BaseModel

from evaluation.stats import TimingStats
from evaluation.vis import generate_visualization_html
from navi_bench.base import get_import_path, instantiate

T = TypeVar("T")


def log_formatter(record: dict, *, colorize: bool = True) -> str:
    """Format log messages. Used by both global and task-specific log handlers."""
    extra = record["extra"]
    if colorize:
        result = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{file}</cyan>:<cyan>{line}</cyan> | "
        )
        if "task_id" in extra:
            result += "<magenta>{extra[task_id]}</magenta> | "
        if "attempt" in extra:
            result += "<blue>{extra[attempt]}</blue> | "
        result += "<level>{message}</level>\n{exception}"
    else:
        result = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {file}:{line} | "
        if "task_id" in extra:
            result += "{extra[task_id]} | "
        if "attempt" in extra:
            result += "{extra[attempt]} | "
        result += "{message}\n{exception}"
    return result


class Recorder:
    def __init__(self, save_dir: str, task_id: str):
        self.save_dir = save_dir
        self.task_id = task_id
        self.item_dir = osp.join(save_dir, task_id)
        os.makedirs(self.item_dir, exist_ok=True)

    def _log_filter(self, record: dict) -> bool:
        return record["extra"].get("task_id") == self.task_id

    @contextmanager
    def logging(self):
        log_path = osp.join(self.item_dir, "task.log")
        handler_id = logger.add(
            log_path, format=functools.partial(log_formatter, colorize=False), filter=self._log_filter, level="DEBUG"
        )
        try:
            yield
        finally:
            logger.remove(handler_id)

    async def _save_text(self, filename: str, build_content: Callable[[], str], kind: str) -> None:
        save_path = osp.join(self.item_dir, filename)
        try:
            content = build_content()
            async with aiofiles.open(save_path, "w") as f:
                await f.write(content)
        except Exception:
            logger.opt(exception=True).error(f"Failed to save {kind} to: {save_path}")

    async def _save_json(self, filename: str, build_data: Callable[[], dict], kind: str) -> None:
        await self._save_text(filename, lambda: json.dumps(build_data(), indent=2), kind)

    async def save_html(
        self,
        messages: list[dict],
        result: BaseModel | None = None,
        coord_space_width: int | None = None,
        coord_space_height: int | None = None,
    ) -> None:
        def build_html() -> str:
            kwargs = {"task_id": self.task_id, "messages": messages, "result": result}
            if coord_space_width is not None:
                kwargs["coord_space_width"] = coord_space_width
            if coord_space_height is not None:
                kwargs["coord_space_height"] = coord_space_height
            return generate_visualization_html(**kwargs)

        await self._save_text("visualization.html", build_html, "HTML visualization")

    async def save_messages(self, messages: list[dict]) -> None:
        def serialize(obj):
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            elif hasattr(obj, "__dict__"):
                return obj.__dict__
            return str(obj)

        def build_content() -> str:
            return "\n".join(json.dumps(message, default=serialize) for message in messages)

        await self._save_text("messages.jsonl", build_content, "messages")

    async def _load_json(self, filename: str, deserialize: Callable[[dict], T], kind: str) -> T | None:
        load_path = osp.join(self.item_dir, filename)
        if not osp.exists(load_path):
            return None
        try:
            async with aiofiles.open(load_path, "r") as f:
                content = await f.read()
            return deserialize(json.loads(content))
        except Exception:
            logger.opt(exception=True).error(f"Failed to load {kind} from: {load_path}")
            return None

    async def save_result(self, result: BaseModel) -> None:
        await self._save_json(
            "result.json",
            lambda: {"_target_": get_import_path(type(result)), **result.model_dump(mode="json", exclude_none=True)},
            "result",
        )

    async def load_result(self) -> BaseModel | None:
        return await self._load_json("result.json", instantiate, "result")

    async def save_usage(self, usage: BaseModel) -> None:
        await self._save_json("usage.json", lambda: usage.model_dump(mode="json"), "usage")

    async def load_usage(self, cls: type[BaseModel]) -> BaseModel | None:
        return await self._load_json("usage.json", cls.model_validate, "usage")

    async def save_timing(self, timing: TimingStats) -> None:
        await self._save_json("timing.json", lambda: timing.model_dump(mode="json"), "timing")

    async def load_timing(self) -> TimingStats | None:
        return await self._load_json("timing.json", TimingStats.model_validate, "timing")
