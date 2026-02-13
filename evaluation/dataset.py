from collections import defaultdict

from datasets import concatenate_datasets, disable_caching, load_dataset
from loguru import logger

from navi_bench.base import DatasetItem


async def build_dataset(config) -> list[DatasetItem]:
    """Build and filter the dataset based on config.

    Config must have: dataset_name, dataset_splits, dataset_revision,
    dataset_include_domains, dataset_include_task_ids,
    dataset_max_samples_per_domain, dataset_max_samples.
    """
    disable_caching()

    dataset = concatenate_datasets(
        [
            load_dataset(config.dataset_name, split=split, revision=config.dataset_revision)
            for split in config.dataset_splits
        ]
    )
    logger.info(
        f"Loaded {len(dataset)} raw tasks in total from {config.dataset_name}, "
        f"splits={config.dataset_splits}, "
        f"revision={config.dataset_revision}"
    )

    _per_domain_counter = defaultdict(int)
    _overall_counter = 0

    def _sample_fn(item: dict) -> bool:
        if config.dataset_include_domains and item["domain"] not in config.dataset_include_domains:
            return False

        if config.dataset_include_task_ids and item["task_id"] not in config.dataset_include_task_ids:
            return False

        nonlocal _per_domain_counter
        _per_domain_counter[item["domain"]] += 1
        if (
            config.dataset_max_samples_per_domain
            and _per_domain_counter[item["domain"]] > config.dataset_max_samples_per_domain
        ):
            return False

        nonlocal _overall_counter
        _overall_counter += 1

        if config.dataset_max_samples and _overall_counter > config.dataset_max_samples:
            return False
        return True

    dataset = dataset.filter(_sample_fn)
    logger.info(f"Sampled {len(dataset)} tasks eventually for evaluation")

    return [DatasetItem.model_validate(item) for item in dataset]
