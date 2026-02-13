import functools
import json
import os
from contextlib import contextmanager
from os import path as osp

import aiofiles
from loguru import logger
from pydantic import BaseModel

from evaluation.stats import TimingStats
from evaluation.vis import generate_visualization_html
from navi_bench.base import get_import_path, instantiate


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

    async def save_html(
        self,
        messages: list[dict],
        result: BaseModel | None = None,
        coord_space_width: int | None = None,
        coord_space_height: int | None = None,
    ) -> None:
        save_path = osp.join(self.item_dir, "visualization.html")
        try:
            kwargs = {"task_id": self.task_id, "messages": messages, "result": result}
            if coord_space_width is not None:
                kwargs["coord_space_width"] = coord_space_width
            if coord_space_height is not None:
                kwargs["coord_space_height"] = coord_space_height
            html_content = generate_visualization_html(**kwargs)
            async with aiofiles.open(save_path, "w") as f:
                await f.write(html_content)
        except Exception:
            logger.opt(exception=True).error(f"Failed to save HTML visualization to: {save_path}")

    async def save_messages(self, messages: list[dict]) -> None:
        save_path = osp.join(self.item_dir, "messages.jsonl")
        try:

            def serialize(obj):
                if hasattr(obj, "model_dump"):
                    return obj.model_dump()
                elif hasattr(obj, "__dict__"):
                    return obj.__dict__
                return str(obj)

            async with aiofiles.open(save_path, "w") as f:
                lines = [json.dumps(message, default=serialize) for message in messages]
                await f.write("\n".join(lines))
        except Exception:
            logger.opt(exception=True).error(f"Failed to save messages to: {save_path}")

    async def save_result(self, result: BaseModel) -> None:
        save_path = osp.join(self.item_dir, "result.json")
        try:
            dic = {"_target_": get_import_path(type(result)), **result.model_dump(mode="json", exclude_none=True)}
            async with aiofiles.open(save_path, "w") as f:
                await f.write(json.dumps(dic, indent=2))
        except Exception:
            logger.opt(exception=True).error(f"Failed to save result to: {save_path}")

    async def load_result(self) -> BaseModel | None:
        load_path = osp.join(self.item_dir, "result.json")
        if not osp.exists(load_path):
            return None
        try:
            async with aiofiles.open(load_path, "r") as f:
                content = await f.read()
            dic = json.loads(content)
            return instantiate(dic)
        except Exception:
            logger.opt(exception=True).error(f"Failed to load result from: {load_path}")
            return None

    async def save_usage(self, usage: BaseModel) -> None:
        save_path = osp.join(self.item_dir, "usage.json")
        try:
            async with aiofiles.open(save_path, "w") as f:
                await f.write(json.dumps(usage.model_dump(mode="json"), indent=2))
        except Exception:
            logger.opt(exception=True).error(f"Failed to save usage to: {save_path}")

    async def load_usage(self, cls: type[BaseModel]) -> BaseModel | None:
        load_path = osp.join(self.item_dir, "usage.json")
        if not osp.exists(load_path):
            return None
        try:
            async with aiofiles.open(load_path, "r") as f:
                content = await f.read()
            return cls.model_validate(json.loads(content))
        except Exception:
            logger.opt(exception=True).error(f"Failed to load usage from: {load_path}")
            return None

    async def save_timing(self, timing: TimingStats) -> None:
        save_path = osp.join(self.item_dir, "timing.json")
        try:
            async with aiofiles.open(save_path, "w") as f:
                await f.write(json.dumps(timing.model_dump(mode="json"), indent=2))
        except Exception:
            logger.opt(exception=True).error(f"Failed to save timing to: {save_path}")

    async def load_timing(self) -> TimingStats | None:
        load_path = osp.join(self.item_dir, "timing.json")
        if not osp.exists(load_path):
            return None
        try:
            async with aiofiles.open(load_path, "r") as f:
                content = await f.read()
            return TimingStats.model_validate(json.loads(content))
        except Exception:
            logger.opt(exception=True).error(f"Failed to load timing from: {load_path}")
            return None
