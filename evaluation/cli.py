import argparse
import asyncio
import functools
import inspect
from typing import get_args, get_origin

from pydantic_core import PydanticUndefined

from navi_bench.base import unwrap_optional_type


def cli(fn):
    """Decorator that creates a CLI from the Pydantic Config parameter of an async/sync function.

    Usage:
        @cli
        async def main(config: Config):
            ...

        if __name__ == "__main__":
            main()
    """
    sig = inspect.signature(fn)
    config_cls = list(sig.parameters.values())[0].annotation

    @functools.wraps(fn)
    def wrapper():
        parser = argparse.ArgumentParser(description=fn.__doc__)

        for name, field_info in config_cls.model_fields.items():
            if field_info.default is not PydanticUndefined:
                default = field_info.default
            elif field_info.default_factory is not None:
                default = field_info.default_factory()
            else:
                default = None

            kwargs = _build_argparse_kwargs(field_info.annotation, default)
            if field_info.description:
                kwargs["help"] = field_info.description
            parser.add_argument(f"--{name}", **kwargs)

        args = parser.parse_args()
        config = config_cls.model_validate(vars(args))

        if asyncio.iscoroutinefunction(fn):
            asyncio.run(fn(config))
        else:
            fn(config)

    return wrapper


def _build_argparse_kwargs(annotation, default, *, nullable: bool = False) -> dict:
    kwargs = {"default": default}

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Handle T | None or Optional[T]
    unwrapped, is_optional = unwrap_optional_type(annotation)
    if is_optional:
        return _build_argparse_kwargs(unwrapped, default, nullable=True)

    # Handle list[T]
    if origin is list:
        inner_type = args[0] if args else str
        kwargs["nargs"] = "*" if nullable else "+"
        kwargs["type"] = inner_type
        return kwargs

    # Handle bool
    if annotation is bool:
        kwargs["action"] = argparse.BooleanOptionalAction
        return kwargs

    # Handle basic types
    if annotation in (str, int, float):
        kwargs["type"] = annotation
        return kwargs

    # Fallback
    kwargs["type"] = str
    return kwargs
