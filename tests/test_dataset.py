"""Characterization tests for ``evaluation/dataset.py::build_dataset``'s sampling filter.

Pins ``_sample_fn``'s domain/task-id inclusion filters and its per-domain and overall
sample caps -- in particular that ``_overall_counter`` only advances past items that
already cleared the domain/task-id/per-domain-cap checks, so the interaction between
``dataset_max_samples_per_domain`` and ``dataset_max_samples`` is order-dependent.
"""

from dataclasses import dataclass

from datasets import Dataset

from evaluation.dataset import build_dataset
from tests.conftest import run_async


@dataclass
class _FakeDatasetBuildConfig:
    dataset_item_json: str | None = None
    dataset_name: str = "fake/dataset"
    dataset_splits: tuple[str, ...] = ("test",)
    dataset_revision: str | None = None
    dataset_include_domains: list[str] | None = None
    dataset_include_task_ids: list[str] | None = None
    dataset_max_samples_per_domain: int | None = None
    dataset_max_samples: int | None = None


def _item(domain: str, index: int) -> dict:
    return {
        "task_id": f"fake/{domain}/{index}",
        "task_generation_config_json": "{}",
        "env": "sim",
        "domain": domain,
        "l1_category": "travel",
    }


def _build(monkeypatch, items: list[dict], config: _FakeDatasetBuildConfig) -> list[str]:
    dataset = Dataset.from_list(items)
    monkeypatch.setattr("evaluation.dataset.load_dataset", lambda *args, **kwargs: dataset)
    monkeypatch.setattr("evaluation.dataset.concatenate_datasets", lambda datasets: datasets[0])
    result = run_async(build_dataset(config))
    return [dataset_item.task_id for dataset_item in result]


class TestBuildDatasetSampling:
    def test_no_filters_keeps_every_item(self, monkeypatch):
        items = [_item("resy", 0), _item("resy", 1), _item("craigslist", 0)]
        assert _build(monkeypatch, items, _FakeDatasetBuildConfig()) == [
            "fake/resy/0",
            "fake/resy/1",
            "fake/craigslist/0",
        ]

    def test_include_domains_filters_out_other_domains(self, monkeypatch):
        items = [_item("resy", 0), _item("craigslist", 0)]
        config = _FakeDatasetBuildConfig(dataset_include_domains=["resy"])
        assert _build(monkeypatch, items, config) == ["fake/resy/0"]

    def test_include_task_ids_filters_to_exact_ids(self, monkeypatch):
        items = [_item("resy", 0), _item("resy", 1)]
        config = _FakeDatasetBuildConfig(dataset_include_task_ids=["fake/resy/1"])
        assert _build(monkeypatch, items, config) == ["fake/resy/1"]

    def test_max_samples_per_domain_caps_each_domain_independently(self, monkeypatch):
        items = [_item("resy", 0), _item("resy", 1), _item("craigslist", 0), _item("craigslist", 1)]
        config = _FakeDatasetBuildConfig(dataset_max_samples_per_domain=1)
        assert _build(monkeypatch, items, config) == ["fake/resy/0", "fake/craigslist/0"]

    def test_max_samples_caps_overall_count_across_domains(self, monkeypatch):
        items = [_item("resy", 0), _item("resy", 1), _item("craigslist", 0)]
        config = _FakeDatasetBuildConfig(dataset_max_samples=2)
        assert _build(monkeypatch, items, config) == ["fake/resy/0", "fake/resy/1"]

    def test_max_samples_does_not_count_items_dropped_by_per_domain_cap(self, monkeypatch):
        # The 2nd "resy" item is dropped by the per-domain cap before the overall counter
        # advances, so it does not consume one of the two overall slots below.
        items = [_item("resy", 0), _item("resy", 1), _item("craigslist", 0), _item("craigslist", 1)]
        config = _FakeDatasetBuildConfig(dataset_max_samples_per_domain=1, dataset_max_samples=2)
        assert _build(monkeypatch, items, config) == ["fake/resy/0", "fake/craigslist/0"]

    def test_zero_max_samples_is_treated_as_unset(self, monkeypatch):
        items = [_item("resy", 0), _item("resy", 1)]
        config = _FakeDatasetBuildConfig(dataset_max_samples=0)
        assert _build(monkeypatch, items, config) == ["fake/resy/0", "fake/resy/1"]
