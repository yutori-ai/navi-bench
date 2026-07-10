"""Characterization test for ``evaluation.browser.get_prepare_page_js``.

Pins the pre-refactor behavior (reading ``prepare_page.js`` from the same directory as
``evaluation/browser.py``, open-coded via ``os.path``) before switching the implementation to
delegate to ``navi_bench.base.read_sidecar`` -- the same sidecar-file-read helper already used by
``navi_bench/resy/resy_url_match.py`` and ``navi_bench/opentable/opentable_info_gathering.py``.
"""

from pathlib import Path

from evaluation.browser import get_prepare_page_js


class TestGetPreparePageJs:
    def test_returns_prepare_page_js_contents(self):
        expected = (Path(__file__).parent.parent / "evaluation" / "prepare_page.js").read_text()

        assert get_prepare_page_js() == expected

    def test_result_is_cached(self):
        # get_prepare_page_js is decorated with functools.cache; repeated calls must return
        # the exact same string object, not just an equal one.
        assert get_prepare_page_js() is get_prepare_page_js()
