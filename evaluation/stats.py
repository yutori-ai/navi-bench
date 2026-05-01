from collections import defaultdict
from statistics import median

from loguru import logger
from pydantic import BaseModel, Field, computed_field
from tabulate import SEPARATING_LINE, tabulate

from navi_bench.base import DatasetItem


class BaseTokenUsage(BaseModel):
    """Base class for token usage tracking.

    Subclasses should implement:
    - __add__: combine two usage instances
    - show_summary: display a usage summary for a list of usages
    """

    def __add__(self, other: "BaseTokenUsage") -> "BaseTokenUsage":
        raise NotImplementedError

    @classmethod
    def show_summary(cls, usages: list["BaseTokenUsage"]) -> None:
        raise NotImplementedError


class Crashed(BaseModel):
    score: float = 0.0
    exception: str | None = None
    traceback: str | None = None


def log_section_header(title: str, width: int = 60) -> None:
    """Log a banner header surrounded by ``=`` rules at the given width."""
    logger.info("")
    logger.info("=" * width)
    logger.info(title)
    logger.info("=" * width)


class TimingStats(BaseModel):
    """Aggregate timing stats for a sequence of API calls.

    ``times_ms`` is the single source of truth. Aggregate views (``call_count``,
    ``total_time_ms``, ``min_time_ms``, ``max_time_ms``) are computed from it,
    so they cannot drift out of sync with the underlying samples and ``add_call``
    / ``merge`` only need to maintain one field. The aggregates remain part of
    the serialized schema via ``@computed_field`` so existing ``timing.json``
    consumers see the same JSON shape.
    """

    times_ms: list[float] = Field(default_factory=list)

    def add_call(self, time_ms: float) -> None:
        self.times_ms.append(time_ms)

    def merge(self, other: "TimingStats") -> "TimingStats":
        return TimingStats(times_ms=self.times_ms + other.times_ms)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def call_count(self) -> int:
        return len(self.times_ms)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_time_ms(self) -> float:
        return sum(self.times_ms)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def min_time_ms(self) -> float:
        return min(self.times_ms) if self.times_ms else float("inf")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_time_ms(self) -> float:
        return max(self.times_ms) if self.times_ms else 0.0

    @property
    def avg_time_ms(self) -> float:
        return self.total_time_ms / self.call_count if self.call_count > 0 else 0.0

    @property
    def median_time_ms(self) -> float:
        if not self.times_ms:
            return 0.0
        return median(self.times_ms)

    @property
    def p95_time_ms(self) -> float:
        if not self.times_ms:
            return 0.0
        sorted_times = sorted(self.times_ms)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]


def show_timing_summary(timings: list[TimingStats]) -> None:
    total_timing = TimingStats()
    for timing in timings:
        total_timing = total_timing.merge(timing)

    if total_timing.call_count == 0:
        log_section_header("Timing Summary: No API calls recorded")
        return

    tasks_with_calls = sum(1 for t in timings if t.call_count > 0)
    avg_calls_per_task = total_timing.call_count / tasks_with_calls if tasks_with_calls > 0 else 0
    avg_time_per_task = total_timing.total_time_ms / tasks_with_calls if tasks_with_calls > 0 else 0
    total_time_s = total_timing.total_time_ms / 1000

    log_section_header("Timing Summary")
    logger.info(f"  Total API calls:           {total_timing.call_count:>12,}")
    logger.info(f"  Total time:                {total_timing.total_time_ms:>12,.0f} ms ({total_time_s:.1f} s)")
    logger.info("-" * 60)
    logger.info(f"  Avg time per call:         {total_timing.avg_time_ms:>12,.0f} ms")
    logger.info(f"  Median time per call:      {total_timing.median_time_ms:>12,.0f} ms")
    logger.info(f"  Min time per call:         {total_timing.min_time_ms:>12,.0f} ms")
    logger.info(f"  Max time per call:         {total_timing.max_time_ms:>12,.0f} ms")
    logger.info(f"  P95 time per call:         {total_timing.p95_time_ms:>12,.0f} ms")
    logger.info("-" * 60)
    logger.info(f"  Avg calls per task:        {avg_calls_per_task:>12.1f}")
    logger.info(f"  Avg time per task:         {avg_time_per_task:>12,.0f} ms ({avg_time_per_task / 1000:.1f} s)")
    logger.info(f"  Tasks with API calls:      {tasks_with_calls:>12,}")


def show_results(dataset: list[DatasetItem], results: list[BaseModel | Crashed]) -> None:
    log_section_header("Detailed Results", width=90)

    per_domain_difficulty: dict[str, dict[str, list[tuple[float, bool]]]] = defaultdict(lambda: defaultdict(list))

    for i, (item, result) in enumerate(zip(dataset, results)):
        difficulty = item.suggested_difficulty or "unknown"
        crashed = isinstance(result, Crashed)
        suffix = " (crashed)" if crashed else ""
        log_fn = logger.error if crashed else logger.info
        log_fn(f"  [{i:3d}] {item.task_id:60s} | {difficulty:6s} | score = {result.score:4.2f}{suffix}")
        per_domain_difficulty[item.domain][difficulty].append((result.score, crashed))

    def _compute_metrics(entries: list[tuple[float, bool]]) -> tuple[int, int, str, str, str]:
        if not entries:
            return 0, 0, "N/A", "N/A", "N/A"

        n = len(entries)
        n_crashed = sum(1 for _, crashed in entries if crashed)
        n_finished = n - n_crashed

        lower_sum = sum(score if not crashed else 0.0 for score, crashed in entries)
        lower_bound = f"{lower_sum / n:.2f}"

        if n_finished > 0:
            success_sum = sum(score for score, crashed in entries if not crashed)
            excluding_crashed = f"{success_sum / n_finished:.2f}"
        else:
            excluding_crashed = "N/A"

        upper_sum = sum(score if not crashed else 1.0 for score, crashed in entries)
        upper_bound = f"{upper_sum / n:.2f}"

        return n_finished, n_crashed, lower_bound, excluding_crashed, upper_bound

    log_section_header("Summary (Lower Bound: crashed=0.0, Upper Bound: crashed=1.0, Excluding: no crashed)", width=90)

    table_rows = []
    all_entries: list[tuple[float, bool]] = []
    difficulties_order = ["easy", "medium", "hard", "unknown"]

    for domain in sorted(per_domain_difficulty.keys()):
        difficulty_data = per_domain_difficulty[domain]

        domain_entries: list[tuple[float, bool]] = []
        for diff in difficulties_order:
            if diff in difficulty_data:
                domain_entries.extend(difficulty_data[diff])

        all_entries.extend(domain_entries)
        n_finished, n_crashed, lower, excluding, upper = _compute_metrics(domain_entries)

        table_rows.append([domain, n_finished, n_crashed, lower, excluding, upper])

        for diff in difficulties_order:
            if diff in difficulty_data:
                diff_entries = difficulty_data[diff]
                d_n_finished, d_n_crashed, d_lower, d_excluding, d_upper = _compute_metrics(diff_entries)
                table_rows.append([f"  └─ {diff}", d_n_finished, d_n_crashed, d_lower, d_excluding, d_upper])

        table_rows.append(SEPARATING_LINE)

    total_n_finished, total_n_crashed, total_lower, total_excluding, total_upper = _compute_metrics(all_entries)
    table_rows.append(["Overall", total_n_finished, total_n_crashed, total_lower, total_excluding, total_upper])

    headers = ["Domain", "n_finished", "n_crashed", "Lower Bound", "Excl. Crashed", "Upper Bound"]
    table_str = tabulate(
        table_rows,
        headers=headers,
        tablefmt="simple",
        stralign="right",
        numalign="right",
        colalign=("left",),
        disable_numparse=[3, 4, 5],
    )

    for line in table_str.split("\n"):
        logger.info(line)
