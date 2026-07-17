"""Characterization tests for ``evaluation.custom_task``.

These pin the current behavior of ``generate_task_config`` -- in particular the
``eval_config["_target_"]`` string it embeds, which is derived via
``navi_bench.base.get_import_path(CustomTaskCaptureMetric)`` the way every other domain
matcher's ``generate_task_config``/``build_task_config`` call site does, rather than
hand-written as a literal string that could drift if the class is renamed or moved. Also
pins ``CustomTaskCaptureMetric``'s reset/update/compute score semantics.
"""

import asyncio

from evaluation.custom_task import CustomTaskCaptureMetric, CustomTaskResult, generate_task_config
from navi_bench.base import UserMetadata


class TestGenerateTaskConfig:
    def test_eval_config_target_points_at_capture_metric(self):
        config = generate_task_config(task="do a thing", url="https://example.com")

        assert config.eval_config == {"_target_": "evaluation.custom_task.CustomTaskCaptureMetric"}

    def test_basic_fields_passed_through(self):
        config = generate_task_config(task="do a thing", url="https://example.com", use_cdp=True)

        assert config.task == "do a thing"
        assert config.url == "https://example.com"
        assert config.use_cdp is True

    def test_default_use_cdp_is_false(self):
        config = generate_task_config(task="do a thing", url="https://example.com")

        assert config.use_cdp is False

    def test_missing_user_metadata_defaults(self):
        config = generate_task_config(task="do a thing", url="https://example.com")

        assert config.user_metadata == UserMetadata()

    def test_user_metadata_dict_is_validated(self):
        config = generate_task_config(
            task="do a thing",
            url="https://example.com",
            user_metadata={"location": "New York, NY, United States", "timezone": "America/New_York"},
        )

        assert config.user_metadata.location == "New York, NY, United States"
        assert config.user_metadata.timezone == "America/New_York"


class TestCustomTaskCaptureMetric:
    def test_no_answer_message_scores_zero(self):
        metric = CustomTaskCaptureMetric()

        result = asyncio.run(metric.compute())

        assert result == CustomTaskResult(score=0.0, final_answer=None)

    def test_answer_message_scores_one(self):
        metric = CustomTaskCaptureMetric()
        asyncio.run(metric.update(answer_message="the final answer"))

        result = asyncio.run(metric.compute())

        assert result == CustomTaskResult(score=1.0, final_answer="the final answer")

    def test_update_without_answer_message_kwarg_is_a_noop(self):
        metric = CustomTaskCaptureMetric()
        asyncio.run(metric.update(other_kwarg="ignored"))

        result = asyncio.run(metric.compute())

        assert result == CustomTaskResult(score=0.0, final_answer=None)

    def test_reset_clears_previously_captured_answer(self):
        metric = CustomTaskCaptureMetric()
        asyncio.run(metric.update(answer_message="first"))
        asyncio.run(metric.reset())

        result = asyncio.run(metric.compute())

        assert result == CustomTaskResult(score=0.0, final_answer=None)
