"""Characterization tests for ``Recorder``'s JSON save/load helpers, added ahead of extracting
a shared ``_save_model``/``_load_model`` pair for ``save_usage``/``load_usage`` and
``save_timing``/``load_timing`` (previously four near-identical one-liners differing only in
filename and pydantic model type). ``recorder.py`` had zero prior test coverage.
"""

import json
from os import path as osp

import pytest
from pydantic import BaseModel

from evaluation.recorder import Recorder
from evaluation.stats import TimingStats


class _DummyUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


@pytest.mark.asyncio
async def test_save_usage_then_load_usage_round_trips(tmp_path):
    recorder = Recorder(str(tmp_path), "task-1")
    usage = _DummyUsage(input_tokens=10, output_tokens=20)

    await recorder.save_usage(usage)
    loaded = await recorder.load_usage(_DummyUsage)

    assert loaded == usage


@pytest.mark.asyncio
async def test_save_usage_writes_expected_json_shape(tmp_path):
    recorder = Recorder(str(tmp_path), "task-1")
    usage = _DummyUsage(input_tokens=10, output_tokens=20)

    await recorder.save_usage(usage)

    save_path = osp.join(str(tmp_path), "task-1", "usage.json")
    with open(save_path) as f:
        content = json.load(f)
    assert content == {"input_tokens": 10, "output_tokens": 20}


@pytest.mark.asyncio
async def test_load_usage_returns_none_when_file_missing(tmp_path):
    recorder = Recorder(str(tmp_path), "task-1")

    assert await recorder.load_usage(_DummyUsage) is None


@pytest.mark.asyncio
async def test_load_usage_returns_none_on_invalid_content(tmp_path):
    recorder = Recorder(str(tmp_path), "task-1")
    save_path = osp.join(str(tmp_path), "task-1", "usage.json")
    with open(save_path, "w") as f:
        f.write('{"not_a_valid_field": "oops"')  # malformed JSON

    assert await recorder.load_usage(_DummyUsage) is None


@pytest.mark.asyncio
async def test_save_timing_then_load_timing_round_trips(tmp_path):
    recorder = Recorder(str(tmp_path), "task-1")
    timing = TimingStats()
    timing.add_call(100.0)
    timing.add_call(200.0)

    await recorder.save_timing(timing)
    loaded = await recorder.load_timing()

    assert loaded == timing
    assert loaded.call_count == 2
    assert loaded.total_time_ms == 300.0


@pytest.mark.asyncio
async def test_load_timing_returns_none_when_file_missing(tmp_path):
    recorder = Recorder(str(tmp_path), "task-1")

    assert await recorder.load_timing() is None


@pytest.mark.asyncio
async def test_save_timing_writes_expected_json_shape(tmp_path):
    recorder = Recorder(str(tmp_path), "task-1")
    timing = TimingStats()
    timing.add_call(50.0)

    await recorder.save_timing(timing)

    save_path = osp.join(str(tmp_path), "task-1", "timing.json")
    with open(save_path) as f:
        content = json.load(f)
    assert content["times_ms"] == [50.0]
    assert content["call_count"] == 1
    assert content["total_time_ms"] == 50.0
