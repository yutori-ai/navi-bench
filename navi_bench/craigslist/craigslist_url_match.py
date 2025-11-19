from datetime import datetime
from typing import TypedDict
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from beartype import beartype
from loguru import logger
from pydantic import BaseModel

from navi_bench.base import BaseMetric, BaseTaskConfig, UserMetadata, get_import_path


IGNORE_URL_PARAMS = ("isTrusted",)


class InputDict(TypedDict, total=False):
    url: str


class FinalResult(BaseModel):
    score: float
    reasoning: str


@beartype
class CraigslistUrlMatch(BaseMetric):
    def __init__(self, gt_urls: list[list[str]]) -> None:
        """
        Args:
            gt_urls: list of list of strings, each string is a URL. The two levels of lists are
                for "AND" -> "OR" checking logic, i.e., all the elements in the first level of the list
                need to be covered, and at least one of the elements in the second level of each list
                need to be covered.
        """
        super().__init__()
        self.gt_urls = gt_urls

        self._gt_states = [[self._parse_state(url) for url in urls] for urls in gt_urls]
        self._intermediate_url_to_state = {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(gt_urls={self.gt_urls})"

    async def reset(self) -> None:
        self._intermediate_url_to_state = {}

    async def update(self, **kwargs) -> None:
        inputs: InputDict = kwargs
        url = inputs["url"]
        if url not in self._intermediate_url_to_state:
            state = self._parse_state(url)
            self._intermediate_url_to_state[url] = state
            logger.info(f"CraigslistUrlMatch.update: {url=}, {state=}")

    async def compute(self) -> FinalResult:
        n_covered = 0
        # First level of iteration: all the elements in `self.gt_urls` are required to be covered
        for i, candidate_gt_states in enumerate(self._gt_states):
            # Second level of iteration: good if any of the elements in `candidate_gt_states` is covered
            for j, gt_state in enumerate(candidate_gt_states):
                is_covered = False
                for intermediate_url, intermediate_state in self._intermediate_url_to_state.items():
                    if intermediate_state == gt_state:  # dicts need to be exactly the same
                        is_covered = True
                        n_covered += 1
                        logger.info(
                            f"CraigslistUrlMatch.compute found {i}-th candidate URL covered:\n"
                            f"    intermediate_url: {intermediate_url}\n"
                            f"    gt_url: {self.gt_urls[i][j]}\n"
                            f"    gt_state: {gt_state}"
                        )
                        break
                if is_covered:
                    break

        n_required = len(self._gt_states)
        score = n_covered / max(n_required, 1)
        reasoning = f"Covered {n_covered} out of {n_required} required URLs"
        return FinalResult(score=score, reasoning=reasoning)

    @staticmethod
    def _parse_state(url: str) -> dict:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        query_params = {k: v for k, v in query_params.items() if k not in IGNORE_URL_PARAMS}
        return query_params


def generate_task_config(
    url: str,
    task: str,
    location: str,
    timezone: str,
    gt_urls: list[list[str]],
) -> BaseTaskConfig:
    tz_info = ZoneInfo(timezone)
    timestamp = int(datetime.now(tz_info).timestamp())
    user_metadata = UserMetadata(location=location, timezone=timezone, timestamp=timestamp)

    eval_target = get_import_path(CraigslistUrlMatch)
    eval_config = {"_target_": eval_target, "gt_urls": gt_urls}

    return BaseTaskConfig(url=url, task=task, user_metadata=user_metadata, eval_config=eval_config)


if __name__ == "__main__":
    import json

    from navi_bench.base import DatasetItem, instantiate

    dataset_row = {
        "task_id": "navi_bench/craigslist/sf_rental_search/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.craigslist.craigslist_url_match.generate_task_config",
                "url": "https://sfbay.craigslist.org/search/sfc/apa",
                "task": (
                    "Search for 2+ bedroom rentals within 1 mile of zip code 94043, posted today, that allow pets and "
                    "include in-unit laundry. Extract the posting time, address, rent price, pet details, and URL for "
                    "any new listings found."
                ),
                "location": "San Francisco, CA, United States",
                "timezone": "America/Los_Angeles",
                "gt_urls": [
                    [
                        "https://sfbay.craigslist.org/search/apa?laundry=1&min_bedrooms=2&pets_cat=1&pets_dog=1&postal=94043&postedToday=1&search_distance=1#search=2~gallery~0"
                    ]
                ],
            }
        ),
        "env": "real",
        "domain": "craigslist",
        "l1_category": "realestate",
        "l2_category": "sf_rental_search",
        "suggested_split": "train",
        "suggested_difficulty": "hard",
    }

    dataset_row = {
        "task_id": "navi_bench/craigslist/craigslist_basic_filters/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.craigslist.craigslist_url_match.generate_task_config",
                "url": "https://sfbay.craigslist.org/search/sfc/apa",
                "task": "Search for weekly rentals. Give me a quick overview.",
                "location": "San Francisco, CA, United States",
                "timezone": "America/Los_Angeles",
                "gt_urls": [["https://sfbay.craigslist.org/search/sfc/apa?rent_period=2#search=2~gallery~0"]],
            }
        ),
        "env": "real",
        "domain": "craigslist",
        "l1_category": "realestate",
        "l2_category": "craigslist_basic_filters",
        "suggested_split": "train",
        "suggested_difficulty": "easy",
    }

    dataset_row = {
        "task_id": "navi_bench/craigslist/ny_rental_search/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.craigslist.craigslist_url_match.generate_task_config",
                "url": "https://newyork.craigslist.org/search/mnh/apa",
                "task": (
                    "Look for 1-bedroom apartments in Greenwich Village under $3,500/month, posted today. "
                    "Extract posting time, rent, neighborhood, and URL."
                ),
                "location": "New York, NY, United States",
                "timezone": "America/New_York",
                "gt_urls": [
                    [
                        "https://newyork.craigslist.org/search/mnh/apa?max_bedrooms=1&max_price=3500&min_bedrooms=1&nh=127&postedToday=1#search=2~gallery~0",
                        "https://newyork.craigslist.org/search/mnh/apa?housing_type=1&max_bedrooms=1&max_price=3500&min_bedrooms=1&nh=127&postedToday=1#search=2~gallery~0",
                    ]
                ],
            }
        ),
        "env": "real",
        "domain": "craigslist",
        "l1_category": "realestate",
        "l2_category": "ny_rental_search",
        "suggested_split": "train",
        "suggested_difficulty": "easy",
    }

    dataset_item = DatasetItem.model_validate(dataset_row)
    task_config = dataset_item.generate_task_config()
    evaluator = instantiate(task_config.eval_config)

    print("Loaded dataset item")
    print("-------------------")
    print(dataset_item)
    print()

    print("Generated task config")
    print("---------------------")
    print(task_config)
    print()

    print("Instantiated evaluator")
    print("----------------------")
    print(evaluator)
