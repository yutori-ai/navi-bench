"""Characterization tests for ``evaluation.cli._build_argparse_kwargs``, which shares its
``Optional[T]``/``T | None`` detection with ``navi_bench.base.basic_pydantic_to_hf_features``
via the extracted ``navi_bench.base.unwrap_optional_type`` helper. These pin the pre-refactor
behavior for optional, list, bool, basic, and "not a simple optional" annotations.
"""

import argparse

from evaluation.cli import _build_argparse_kwargs


class TestBuildArgparseKwargs:
    def test_basic_type(self):
        kwargs = _build_argparse_kwargs(str, default="x")

        assert kwargs == {"default": "x", "type": str}

    def test_bool_uses_boolean_optional_action(self):
        kwargs = _build_argparse_kwargs(bool, default=False)

        assert kwargs == {"default": False, "action": argparse.BooleanOptionalAction}

    def test_list_type(self):
        kwargs = _build_argparse_kwargs(list[int], default=None)

        assert kwargs == {"default": None, "nargs": "+", "type": int}

    def test_optional_basic_type_unwraps_and_uses_star_nargs_if_list(self):
        kwargs = _build_argparse_kwargs(int | None, default=None)

        assert kwargs == {"default": None, "type": int}

    def test_optional_list_type_uses_star_nargs(self):
        kwargs = _build_argparse_kwargs(list[str] | None, default=None)

        assert kwargs == {"default": None, "nargs": "*", "type": str}

    def test_unsupported_type_falls_back_to_str(self):
        kwargs = _build_argparse_kwargs(dict, default=None)

        assert kwargs == {"default": None, "type": str}

    def test_non_optional_union_falls_back_to_str(self):
        # A union that isn't a simple `T | None` optional (more than one non-None member)
        # is not unwrapped; it falls through to the str fallback rather than raising.
        kwargs = _build_argparse_kwargs(int | str, default=None)

        assert kwargs == {"default": None, "type": str}
