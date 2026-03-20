from pydantic import BaseModel

from navi_bench.base import BaseMetric, BaseTaskConfig, UserMetadata


class CustomTaskResult(BaseModel):
    score: float = 0.0
    final_answer: str | None = None


class CustomTaskCaptureMetric(BaseMetric):
    def __init__(self) -> None:
        self.answer_message: str | None = None

    async def reset(self) -> None:
        self.answer_message = None

    async def update(self, /, **kwargs) -> None:
        if kwargs.get("answer_message") is not None:
            self.answer_message = kwargs["answer_message"]

    async def compute(self) -> CustomTaskResult:
        return CustomTaskResult(score=1.0 if self.answer_message else 0.0, final_answer=self.answer_message)


class CustomTaskConfig(BaseTaskConfig):
    use_cdp: bool = False


def generate_task_config(
    *,
    task: str,
    url: str,
    user_metadata: dict | None = None,
    use_cdp: bool = False,
) -> CustomTaskConfig:
    return CustomTaskConfig(
        task=task,
        url=url,
        user_metadata=UserMetadata.model_validate(user_metadata or {}),
        eval_config={"_target_": "evaluation.custom_task.CustomTaskCaptureMetric"},
        use_cdp=use_cdp,
    )
