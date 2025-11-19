import asyncio
import importlib
import json
import random
import types
from datetime import datetime
from functools import cached_property
from typing import Any, Awaitable, Callable, Iterable, Type, TypeVar, Union, get_args, get_origin

from datasets import Features, Value
from loguru import logger
from pydantic import BaseModel, Field


T = TypeVar("T")


def get_import_path(obj: Any) -> str:
    """Get the import path of an object."""
    return f"{obj.__module__}.{obj.__qualname__}"


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


def basic_pydantic_to_hf_features(model_class: Type[BaseModel]) -> Features:
    """
    Basic function to convert a pydantic model to a HuggingFace Features dictionary.
    It only supports fields of basic types or nested pydantic models with basic types, not list, dict, etc.
    """
    features_dict = {}
    for name, field in model_class.model_fields.items():
        field_type = field.annotation

        # unwrap the optional field
        if get_origin(field_type) in (Union, types.UnionType):
            args = tuple(a for a in get_args(field_type) if a is not type(None))
            if len(args) == 1 and len(get_args(field_type)) == 2:
                field_type = args[0]
            else:
                raise ValueError(f"Unexpected union type: {field_type}")

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


def async_retry_with_exponential_backoff(
    max_retries: int = 3,
    delay: float = 1,
    exponential_base: float = 2,
    jitter: bool = True,
    allowed_exceptions: tuple[type[Exception], ...] = (Exception,),
    should_retry_fn: Callable[[Any], bool] | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Retry an async function with exponential backoff."""

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # Initialize variables
            num_retries = 0
            current_delay = delay

            # Loop until a successful response or max_retries is hit or an exception is raised
            while True:
                try:
                    result = await func(*args, **kwargs)

                    if should_retry_fn is None:
                        return result

                    if should_retry_fn(result):
                        num_retries += 1
                        if num_retries > max_retries:
                            return result
                        current_delay *= exponential_base * (1 + jitter * random.random())
                        await asyncio.sleep(current_delay)
                        continue
                    else:
                        return result

                # Retry on specified errors
                except allowed_exceptions as e:
                    # Increment retries
                    num_retries += 1

                    # Check if max retries has been reached
                    if num_retries > max_retries:
                        raise Exception(f"Maximum number of retries ({max_retries}) exceeded.") from e

                    # Increment the delay
                    current_delay *= exponential_base * (1 + jitter * random.random())

                    logger.info(
                        f"Failed to call {func.__name__}. Retrying in {current_delay} seconds. Error: {repr(e)}"
                    )
                    # Sleep for the delay
                    await asyncio.sleep(current_delay)

                # Raise exceptions for any errors not specified
                except Exception as e:
                    raise e

        return wrapper

    return decorator


class UserMetadata(BaseModel):
    location: str = Field(description="Location of the user", default="San Francisco, CA, United States")
    timezone: str = Field(description="Timezone of the user", default="America/Los_Angeles")
    timestamp: int = Field(default_factory=lambda: int(datetime.now().timestamp()))


class BaseTaskConfig(BaseModel):
    task: str
    url: str
    user_metadata: UserMetadata
    eval_config: dict[str, Any]


class BaseMetric:
    async def update(self, /, **kwargs) -> Any: ...

    async def compute(self) -> Any: ...

    async def reset(self) -> None: ...


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
