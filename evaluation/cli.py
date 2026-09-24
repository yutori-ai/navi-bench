import argparse
import asyncio
import functools
import inspect
from typing import Any, get_args, get_origin

from pydantic.fields import FieldInfo
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
            default = _resolve_field_default(field_info)
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


def _resolve_field_default(field_info: FieldInfo) -> Any:
    """Resolve a pydantic field's CLI default, calling ``default_factory`` when present.

    Delegates to ``FieldInfo.get_default(call_default_factory=True)`` instead of the
    hand-rolled ``default if set else (default_factory() if set else None)`` chain.
    A required field (no default, no default_factory) resolves to pydantic's own
    ``PydanticUndefined`` sentinel there, which is normalized to ``None`` here to match
    the previous fallback and argparse's usual "no default" value.
    """
    default = field_info.get_default(call_default_factory=True)
    return None if default is PydanticUndefined else default


def _build_argparse_kwargs(annotation, default, *, nullable: bool = False) -> dict[str, object]:
    kwargs: dict[str, object] = {"default": default}

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
