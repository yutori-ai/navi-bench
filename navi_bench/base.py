import importlib
import json
import types
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any, TypedDict, TypeVar, Union, get_args, get_origin
from urllib.parse import ParseResult, parse_qs, urlparse

from datasets import Features, Value
from loguru import logger
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field


def get_import_path(obj: Any) -> str:
    """Get the import path of an object."""
    return f"{obj.__module__}.{obj.__qualname__}"


def read_sidecar(module_file: str, filename: str) -> str:
    """Read a sidecar file located next to ``module_file`` (typically ``__file__``)."""
    return (Path(module_file).parent / filename).read_text()


def read_sidecar_with_shared_js_prefix(
    module_file: str, filename: str, *, shared_filename: str = "../dom_visibility.js"
) -> str:
    """Read a sidecar JS file, prefixed with a shared helper script it relies on via closure.

    Centralizes the "read the shared helper, then this script, join with a newline" pattern
    duplicated across verifiers whose JS depends on ``dom_visibility.js``'s ``isVisible``.
    """
    return f"{read_sidecar(module_file, shared_filename)}\n{read_sidecar(module_file, filename)}"


async def safe_evaluate(
    page: Any,
    script: str,
    *,
    default: Any,
    log_message: str,
    log_fn: Callable[[str], None] = logger.warning,
    on_success: Callable[[Any], None] | None = None,
) -> Any:
    """Run ``page.evaluate(script)``, returning ``default`` and logging on failure.

    A live page can navigate away or throw a JS error mid-evaluation while a task
    verifier is polling it; callers should degrade gracefully instead of crashing the
    whole verification step. ``on_success`` (if given) is invoked with the evaluated
    value only when evaluation succeeds, matching call sites that previously logged
    only on the happy path.
    """
    try:
        value = await page.evaluate(script)
    except PlaywrightError as exc:
        log_fn(f"{log_message}: {exc}")
        return default
    if on_success is not None:
        on_success(value)
    return value


async def safe_update(
    evaluator: Any,
    *,
    log_fn: Callable[[Exception], None],
    **kwargs: Any,
) -> None:
    """Call ``evaluator.update(**kwargs)``, logging via ``log_fn`` instead of raising.

    A live page can navigate away or throw mid-update while a task verifier polls it;
    callers (a human-agent-loop step and an N1 eval step, previously each hand-rolling
    this try/except) should degrade gracefully instead of crashing the whole loop.
    ``log_fn`` receives the caught exception so callers can format their own message
    (and, e.g., call ``logger.opt(exception=True)`` from within the still-live except
    context).
    """
    try:
        await evaluator.update(**kwargs)
    except Exception as exc:
        log_fn(exc)


def strip_url_scheme(url: str) -> str:
    """Strip http(s):// scheme and a leading www. prefix for URL normalization.

    Assumes the input has already been lowercased; the common url.lower().strip()
    step is performed by callers.
    """
    for scheme in ("https://", "http://"):
        url = url.removeprefix(scheme)
    return url.removeprefix("www.")


def basic_normalize_url(url: str, target_domain: str) -> tuple[ParseResult | None, str]:
    """Apply the opening of URL normalization shared across navi-bench domain matchers.

    Lowercases, strips http(s)://www., and runs ``urlparse`` on the result. When the URL's
    netloc matches ``target_domain``, returns ``(parsed, "")`` so the caller can proceed with
    its domain-specific normalization. Otherwise returns ``(None, fallback)`` where fallback
    is a basic-normalized "netloc + path[?query]" string with any trailing slash stripped —
    this is the form domain matchers return as-is for off-domain URLs.

    Empty input returns ``(None, "")``.
    """
    if not url:
        return None, ""

    normalized = strip_url_scheme(url.lower().strip())
    parsed = urlparse("http://" + normalized)

    if target_domain not in parsed.netloc:
        result = parsed.netloc + parsed.path
        if parsed.query:
            result += "?" + parsed.query
        return None, result.rstrip("/")
    return parsed, ""


def fractional_coverage_score(n_covered: int, n_total: int) -> float:
    """Fraction of required items covered, i.e. ``n_covered / n_total``.

    Guards the zero-required edge case (treated as fully covered rather than raising
    ``ZeroDivisionError``) the same way domain matchers with "cover N required items"
    scoring do, e.g. craigslist's AND-of-OR URL matching and opentable's multi-query
    coverage checks.
    """
    return n_covered / max(n_total, 1)


def hour_to_12h_period(hour: int) -> tuple[int, str]:
    """Convert a 24-hour ``hour`` (0-23) to its 12-hour display value and AM/PM period.

    Returns ``(display_hour, period)`` with ``period`` lowercase (``"am"``/``"pm"``);
    callers needing uppercase can ``.upper()`` it.
    """
    return hour % 12 or 12, "pm" if hour >= 12 else "am"


def parse_filtered_query_params(query: str, ignored: Iterable[str]) -> dict[str, list[str]]:
    """Parse a URL query string and drop keys in ``ignored``.

    Centralizes the ``parse_qs(...) -> dict-comp filter`` pattern that
    domain matchers use to canonicalize URL state by stripping noise
    parameters (e.g. tracking flags, trusted-source markers) before
    comparison.
    """
    return {k: v for k, v in parse_qs(query).items() if k not in ignored}


_T = TypeVar("_T")


def unwrap_single_template_query(
    template_query: list[list[_T]],
    *,
    group_message: str,
    item_message: str,
) -> _T:
    """Validate ``template_query`` is a single query group containing a single item, then return it.

    Centralizes the "assert exactly one query group, assert exactly one item within it, then
    unwrap to ``template_query[0][0]``" precondition that opentable's and resy's
    ``_render_placeholders_in_queries_all`` each repeated verbatim (mode='all' multi-date
    expansion only supports a single templated query/URL) before diverging into their own
    per-placeholder expansion logic. ``group_message``/``item_message`` are the exact
    ``AssertionError`` text each caller already used, kept caller-specific since they name the
    domain-specific unit (e.g. "candidate object", "URL").
    """
    assert len(template_query) == 1, group_message
    assert len(template_query[0]) == 1, item_message
    return template_query[0][0]


def omni_import(path: str):
    """
    Import a module, class, function, or attribute given its absolute path.

    Parameters:
        path (str): The absolute path in the form 'package.module.ClassName'
                    or even deeper nested objects.

    Returns:
        Any: The imported module or attribute.

    Raises:
        ImportError: If no valid module or attribute is found.
    """
    # Split the path into parts by dot
    parts = path.split(".")

    # Try progressively shorter module paths until one can be imported
    for i in range(len(parts), 0, -1):
        module_path = ".".join(parts[:i])
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            continue
        else:
            # If there are remaining parts, traverse them as attributes.
            obj = module
            for attr in parts[i:]:
                try:
                    obj = getattr(obj, attr)
                except AttributeError as e:
                    raise ImportError(
                        f"Module '{module_path}' was found, but it does not contain attribute '{attr}'."
                    ) from e
            return obj

    # If we exit the loop, no valid module was found.
    raise ImportError(f"Could not import anything from '{path}'.")


def instantiate(
    config: Any, args: Iterable[Any] | None = None, kwargs: dict | None = None, recursive: bool | None = None
) -> Any:
    """Instantiate an object based on the config

    Args:
        config: The config for the object to be instantiated. If not a dict with "_target_" key, return the config as is
        args: The arguments to pass to the target init function
        kwargs: The keyword arguments to pass to the target init function
        recursive: Whether to recursively instantiate the objects in args and kwargs. If None, use the value of
            "_recursive_" in the config (which defaults to True). Otherwise, override the value of "_recursive_".

    Returns:
        The instantiated object.
    """
    if recursive is False:
        # Override recursive to False. Return the config as is
        return config

    if recursive is None and isinstance(config, dict) and config.get("_recursive_", True) is False:
        # Config node itself specifies recursive=False. Return the config as is
        return config

    # Recursively instantiate by default
    if isinstance(config, (tuple, list)):
        return [instantiate(item, recursive=recursive) for item in config]
    elif isinstance(config, dict):
        if "_target_" in config:
            if args is None:
                args = config.get("_args_", [])
            if kwargs is None:
                kwargs = {k: v for k, v in config.items() if k not in ("_target_", "_args_", "_recursive_")}
            args = [instantiate(arg, recursive=recursive) for arg in args]
            kwargs = {k: instantiate(v, recursive=recursive) for k, v in kwargs.items()}
            return omni_import(config["_target_"])(*args, **kwargs)  # type: ignore
        else:
            return {k: instantiate(v, recursive=recursive) for k, v in config.items()}
    else:
        return config


def unwrap_optional_type(annotation: Any) -> tuple[Any, bool]:
    """Detect a two-member ``T | None`` / ``Optional[T]`` union and unwrap it to ``T``.

    Returns ``(T, True)`` when ``annotation`` is a union with exactly one non-``NoneType``
    member (i.e. a "simple optional"), and ``(annotation, False)`` unchanged otherwise --
    including for non-union annotations and for unions with more than one non-``None``
    member (e.g. ``int | str | None``), which callers should treat as "not a simple
    optional" and handle accordingly (e.g. raise or fall back to a default).
    """
    origin = get_origin(annotation)
    if origin not in (Union, types.UnionType):
        return annotation, False
    non_none = tuple(a for a in get_args(annotation) if a is not type(None))
    if len(non_none) == 1:
        return non_none[0], True
    return annotation, False


def basic_pydantic_to_hf_features(model_class: type[BaseModel]) -> Features:
    """
    Basic function to convert a pydantic model to a HuggingFace Features dictionary.
    It only supports fields of basic types or nested pydantic models with basic types, not list, dict, etc.
    """
    features_dict = {}
    for name, field in model_class.model_fields.items():
        field_type = field.annotation

        # unwrap the optional field
        if get_origin(field_type) in (Union, types.UnionType):
            unwrapped, is_optional = unwrap_optional_type(field_type)
            if not is_optional:
                raise ValueError(f"Unexpected union type: {field_type}")
            field_type = unwrapped

        # recursive if the field is a pydantic model
        if issubclass(field_type, BaseModel):
            features_dict[name] = basic_pydantic_to_hf_features(field_type)
        elif field_type is str:
            features_dict[name] = Value(dtype="string")
        elif field_type is int:
            features_dict[name] = Value(dtype="int64")
        elif field_type is float:
            features_dict[name] = Value(dtype="float64")
        elif field_type is bool:
            features_dict[name] = Value(dtype="bool")
        else:
            raise ValueError(f"Unexpected field type: {field_type}")

    return Features(features_dict)


class UserMetadata(BaseModel):
    location: str = Field(description="Location of the user", default="San Francisco, CA, United States")
    timezone: str = Field(description="Timezone of the user", default="America/Los_Angeles")
    timestamp: int = Field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp()))


class BaseTaskConfig(BaseModel):
    task: str
    url: str
    user_metadata: UserMetadata
    eval_config: dict[str, Any]


def build_task_config(
    *,
    url: str,
    task: str,
    user_metadata: UserMetadata,
    eval_class: Any,
    eval_kwargs: dict[str, Any],
) -> BaseTaskConfig:
    eval_config = {"_target_": get_import_path(eval_class), **eval_kwargs}
    return BaseTaskConfig(url=url, task=task, user_metadata=user_metadata, eval_config=eval_config)


class UrlMetricInput(TypedDict, total=False):
    """Shared `update(**kwargs)` payload for URL-based metrics.

    Domain matchers that branch on the visited browser URL alone (e.g. apartments,
    craigslist, google_flights) declare their `update` kwargs as this type so the
    contract is captured in one place.
    """

    url: str


class BaseMetric:
    async def update(self, /, **kwargs) -> Any: ...

    async def compute(self) -> Any: ...

    async def reset(self) -> None: ...


def repr_with_attr(instance: object, attr_name: str, *, label: str | None = None) -> str:
    """Render ``ClassName(label=value)`` for a single-attribute ``__repr__``.

    Centralizes the ``f"{self.__class__.__name__}({attr}={self.{attr}})"`` one-liner that
    several :class:`BaseMetric` subclasses (apartments, craigslist, resy, opentable, google_flights)
    each hand-rolled. Uses ``type(instance).__name__`` rather than a hardcoded literal so it
    reflects the actual runtime class, including subclasses.

    ``label`` defaults to ``attr_name`` but can be overridden when the displayed name should
    differ from the underlying attribute (e.g. ``GoogleFlightsSearchMatch`` historically
    labeled its ``_gt_base_info`` attribute as ``gt_info``, matching its constructor kwarg).
    """
    return f"{type(instance).__name__}({label if label is not None else attr_name}={getattr(instance, attr_name)})"


class FinalResult(BaseModel):
    """Standard result returned by URL-based domain matchers."""

    score: float  # 1.0 if match, 0.0 if no match
    reasoning: str | None = None


def all_or_nothing_coverage_result(class_name: str, is_covered: list[bool]) -> FinalResult:
    """Score 1.0 iff every element of ``is_covered`` is True, else 0.0.

    Centralizes the ``all(...) -> score -> sum(...) -> FinalResult -> log`` tail that
    domain matchers requiring every ground-truth query/info to be covered (e.g. resy's
    and google_flights') each repeated verbatim, differing only in the class name used
    in the log message. Contrast with :func:`fractional_coverage_score`, which scores
    partial coverage instead of requiring all-or-nothing.
    """
    n_covered = sum(is_covered)
    result = FinalResult(score=1.0 if all(is_covered) else 0.0)
    logger.info(f"{class_name}.compute result: {result} ({n_covered}/{len(is_covered)} queries covered)")
    return result


class DatasetItem(BaseModel):
    # task required fields
    task_id: str = Field(
        description="Unique identifier for the task: <dataset>/<domain>/.../<index>",
        pattern=r"^([a-zA-Z0-9_-]+/)+[0-9]+$",
    )
    task_generation_config_json: str = Field(
        description=(
            "Json-encoded task generation config, an init dict with `_target_` and kwargs for instantiating the task "
            "config"
        ),
    )

    # task metadata fields
    env: str = Field(description="Environment: real | sim", pattern=r"^real|sim$")
    domain: str = Field(description="Website domain: e.g., expedia, google_flights")
    l1_category: str = Field(
        description=(
            "Task first-level category / sector: realestate | food | e_commerce | social | travel. "
            "Use underscore instead of hyphen."
        ),
        pattern=r"^realestate|food|e_commerce|social|travel$",
    )
    l2_category: str | None = Field(
        description=(
            "Task second-level category: sf_rental_search, airline_search_simple, etc. "
            "Use underscore instead of hyphen."
        ),
        default=None,
    )

    # suggested fields
    suggested_difficulty: str | None = Field(
        description="Suggested task difficulty: easy | medium | hard", pattern=r"^easy|medium|hard$", default=None
    )
    suggested_hint: str | None = Field(description="Suggested hint", default=None)
    suggested_max_steps: int | None = Field(description="Suggested max steps", default=None)
    suggested_split: str | None = Field(
        description="Suggested split (while the actual split may be different): train | validation | test",
        pattern=r"^train|validation|test$",
        default=None,
    )

    # additional metadata fields
    metadata_json: str | None = Field(description="Other additional metadata in JSON format", default=None)

    @cached_property
    def task_generation_config(self) -> dict[str, Any]:
        return json.loads(self.task_generation_config_json)

    def generate_task_config(self) -> BaseTaskConfig:
        task_config = instantiate(self.task_generation_config)
        return task_config


def print_dataset_demo(dataset_row: dict) -> None:
    """Validate ``dataset_row`` end-to-end and print the loaded item, task config, and evaluator.

    Used by the ``__main__`` demos in each per-domain matcher module so they share a single
    rendering format.
    """
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
