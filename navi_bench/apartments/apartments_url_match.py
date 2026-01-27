import json
import re
from typing import TypedDict
from urllib.parse import parse_qs, urlencode, urlparse

from beartype import beartype
from loguru import logger
from pydantic import BaseModel

from navi_bench.base import BaseMetric, BaseTaskConfig, get_import_path
from navi_bench.dates import initialize_user_metadata


class InputDict(TypedDict, total=False):
    url: str


class FinalResult(BaseModel):
    score: float  # 1.0 if match, 0.0 if no match


@beartype
class ApartmentsUrlMatch(BaseMetric):
    IGNORED_PARAMS = ("io", "ss")

    def __init__(self, gt_url: str | list[str]) -> None:
        super().__init__()
        # Handle both single URL and list of URLs
        if isinstance(gt_url, str):
            self.gt_urls = [gt_url]
        else:
            self.gt_urls = gt_url
        self._found_match = False

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(gt_urls={self.gt_urls})"

    async def reset(self) -> None:
        self._found_match = False

    async def update(self, **kwargs) -> None:
        inputs: InputDict = kwargs
        url = inputs["url"]

        # Normalize the state URL
        normalized_url = self._normalize_url(url or "")

        # Check against all ground truth URLs
        for gt_url in self.gt_urls:
            normalized_gt_url = self._normalize_url(gt_url)
            if normalized_url == normalized_gt_url:
                self._found_match = True
                logger.info(f"ApartmentsUrlMatch.update found match: {url} matches GT URL: {gt_url}")
                return  # Early exit once match is found

        logger.info(f"ApartmentsUrlMatch.update did not find match: {url}")

    async def compute(self) -> FinalResult:
        score = 1.0 if self._found_match else 0.0
        result = FinalResult(score=score)
        logger.info(f"ApartmentsUrlMatch.compute result: {result}")
        return result

    def _is_location_part(self, part: str) -> bool:
        """Check if a URL part represents a location (neighborhood-city-state format)."""
        if not part or "-" not in part:
            return False

        # Common US state abbreviations
        state_abbreviations = {
            "ca",
            "ny",
            "tx",
            "fl",
            "wa",
            "il",
            "pa",
            "oh",
            "ga",
            "nc",
            "mi",
            "va",
            "tn",
            "in",
            "az",
            "ma",
            "md",
            "mn",
            "co",
            "al",
            "la",
            "ky",
            "or",
            "ok",
            "ct",
            "ia",
            "ms",
            "ar",
            "ut",
            "nv",
            "nm",
            "wv",
            "ne",
            "id",
            "nh",
            "hi",
            "ri",
            "mt",
            "de",
            "sd",
            "nd",
            "ak",
            "dc",
            "vt",
            "wy",
            "me",
            "wi",
            "mo",
            "nj",
            "ks",
            "sc",
        }

        return (
            any(part.endswith(f"-{state}") for state in state_abbreviations) or len(part.split("-")[-1]) == 2
        )  # area_city_state format

    def _normalize_apartment_features(self, part: str) -> str:
        """Normalize apartment features by sorting them alphabetically."""
        apartment_features = {
            "air-conditioning",
            "washer-dryer",
            "dishwasher",
            "parking",
            "fitness-center",
            "pool",
            "gated",
            "garage",
            "walk-in-closets",
            "washer_dryer-hookup",
            "laundry-facilities",
            "utilities-included",
        }

        # Check if this part contains any apartment features
        if not any(feature in part for feature in apartment_features):
            return part

        # Extract features and non-features
        found_features = []
        remaining = part

        # Extract known features (longest first to avoid partial matches)
        for feature in sorted(apartment_features, key=len, reverse=True):
            if feature in remaining:
                found_features.append(feature)
                remaining = remaining.replace(feature, "-").replace("--", "-").strip("-")

        # Get non-feature parts
        non_features = [p for p in remaining.split("-") if p]

        # Sort features alphabetically and combine
        found_features.sort()
        all_parts = non_features + found_features
        return "-".join(p for p in all_parts if p)

    def _extract_locations_from_path(self, path_parts: list[str]) -> tuple[set[str], list[str]]:
        """Extract locations from URL path parts and return (locations, non_location_parts)."""
        locations = set()
        non_location_parts = []

        for part in path_parts:
            if self._is_location_part(part):
                # Replace underscores with hyphens for consistency
                location = part.replace("_", "-")
                locations.add(location)
            else:
                # Normalize apartment features if present
                normalized_part = self._normalize_apartment_features(part)
                non_location_parts.append(normalized_part)

        return locations, non_location_parts

    def _extract_locations_from_query(self, query_params: dict) -> tuple[set[str], dict]:
        """Extract locations from query parameters and return (locations, normalized_params)."""
        locations = set()
        normalized_params = {}

        for key, values in query_params.items():
            if key == "n" and values:
                # Parse locations. parse_qs converts "+" to spaces, so split on '+' and whitespace.
                raw_value = values[0]
                location_parts = [p for p in re.split(r"[+\s]+", raw_value.strip()) if p]
                for loc in location_parts:
                    # Replace underscores with hyphens for consistency
                    normalized_loc = loc.replace("_", "-")
                    locations.add(normalized_loc)
            elif key == "bb":
                # Ignore bb= parameter for URL comparisons
                continue
            else:
                # Keep other parameters as is
                normalized_params[key] = values

        return locations, normalized_params

    def _normalize_url(self, url: str) -> str:
        """Normalize URL by treating locations as sets so order doesn't matter."""
        if not url:
            return ""

        # Basic normalization
        normalized = url.lower().strip()
        normalized = normalized.lstrip("http://").lstrip("https://").lstrip("www.")

        # Parse URL components
        parsed = urlparse("http://" + normalized)

        # Only apply location normalization for apartments.com
        if "apartments.com" not in parsed.netloc:
            # For non-apartments.com URLs, just return basic normalization
            result = parsed.netloc + parsed.path
            if parsed.query:
                result += "?" + parsed.query
            return result.rstrip("/")

        # Extract locations from both path and query parameters
        path_parts = [part for part in parsed.path.split("/") if part]
        path_locations, non_location_path_parts = self._extract_locations_from_path(path_parts)

        query_params = parse_qs(parsed.query)
        query_params = {k: v for k, v in query_params.items() if k not in self.IGNORED_PARAMS}
        query_locations, normalized_params = self._extract_locations_from_query(query_params)

        # Combine all locations and sort for canonical representation
        all_locations = path_locations | query_locations
        sorted_locations = sorted(all_locations)

        if sorted_locations:
            # First location goes in path, rest in query parameter 'n'
            primary_location = sorted_locations[0]
            remaining_locations = sorted_locations[1:]

            # Construct normalized path
            normalized_path_parts = [primary_location] + non_location_path_parts

            # Add remaining locations to query parameter 'n' if there are any
            if remaining_locations:
                normalized_params["n"] = ["+".join(remaining_locations)]
        else:
            normalized_path_parts = non_location_path_parts

        # Reconstruct URL
        normalized_path = "/" + "/".join(normalized_path_parts) if normalized_path_parts else ""
        # Canonicalize query parameter ordering by sorting keys for stable comparisons
        normalized_query = urlencode(sorted(normalized_params.items()), doseq=True) if normalized_params else ""

        result = parsed.netloc + normalized_path
        if normalized_query:
            result += "?" + normalized_query

        return result


def generate_task_config(
    task: str,
    gt_url: list[str],
    location: str,
    timezone: str,
    timestamp: int | None = None,
    url: str = "https://www.apartments.com",
) -> BaseTaskConfig:
    user_metadata = initialize_user_metadata(timezone, location, timestamp)

    eval_target = get_import_path(ApartmentsUrlMatch)
    eval_config = {"_target_": eval_target, "gt_url": gt_url}
    return BaseTaskConfig(url=url, task=task, user_metadata=user_metadata, eval_config=eval_config)


if __name__ == "__main__":
    from navi_bench.base import DatasetItem, instantiate

    dataset_row = {
        "task_id": "navi_bench/apartments/nyc_multi_region_floor_search/1",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.apartments.apartments_url_match.generate_task_config",
                "url": "https://www.apartments.com/",
                "task": (
                    "Find 2-3 bedroom, 2-bath apartments under $7300 in Hudson Yards, Midtown West, or Hell's Kitchen. "
                    "Summarize the listings."
                ),
                "location": "New York, NY, United States",
                "timezone": "America/New_York",
                "gt_url": [
                    "https://www.apartments.com/apartments/hudson-yards-new-york-ny/2-to-3-bedrooms-2-bathrooms-under-7300/?n=midtown-west_new-york_ny+hell%27s-kitchen_new-york_ny",
                    "https://www.apartments.com/hudson-yards-new-york-ny/2-to-3-bedrooms-2-bathrooms-under-7300/?n=midtown-west_new-york_ny+hell%27s-kitchen_new-york_ny",
                ],
            }
        ),
        "env": "real",
        "domain": "apartments",
        "l1_category": "realestate",
        "l2_category": "nyc_multi_region_floor_search",
        "suggested_split": "train",
        "suggested_difficulty": "hard",
    }

    dataset_row = {
        "task_id": "navi_bench/apartments/sf_multi_floorplan_search/1",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.apartments.apartments_url_match.generate_task_config",
                "url": "https://www.apartments.com/",
                "task": (
                    "Look for 1-bedroom, 2-bedroom, and 3-bedroom apartments in Laurel Heights under $6700. "
                    "Share the results."
                ),
                "location": "San Francisco, CA, United States",
                "timezone": "America/Los_Angeles",
                "gt_url": [
                    "https://www.apartments.com/apartments/laurel-heights-san-francisco-ca/1-to-3-bedrooms-under-6700/",
                    "https://www.apartments.com/laurel-heights-san-francisco-ca/1-to-3-bedrooms-under-6700/",
                ],
            }
        ),
        "env": "real",
        "domain": "apartments",
        "l1_category": "realestate",
        "l2_category": "sf_multi_floorplan_search",
        "suggested_split": "train",
        "suggested_difficulty": "medium",
    }

    dataset_row = {
        "task_id": "navi_bench/apartments/nyc_multi_floorplan_search/4",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.apartments.apartments_url_match.generate_task_config",
                "url": "https://www.apartments.com/",
                "task": (
                    "Check for 1-bedroom, and 2-bedroom apartments in Chelsea with rent capped at $5200. "
                    "Provide a recap."
                ),
                "location": "New York, NY, United States",
                "timezone": "America/New_York",
                "gt_url": [
                    "https://www.apartments.com/apartments/chelsea-new-york-ny/1-to-2-bedrooms-under-5200/",
                    "https://www.apartments.com/chelsea-new-york-ny/1-to-2-bedrooms-under-5200/",
                ],
            }
        ),
        "env": "real",
        "domain": "apartments",
        "l1_category": "realestate",
        "l2_category": "nyc_multi_floorplan_search",
        "suggested_split": "train",
        "suggested_difficulty": "medium",
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
