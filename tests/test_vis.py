"""Characterization tests for the per-action "details" strings rendered inside each
step's action card by ``generate_visualization_html``.

These pin the CURRENT behavior of the inline field-by-field ``details.append(...)``
chain in ``generate_visualization_html`` (one ``if "<key>" in action: ...`` per
recognized action field, plus an ``action_type``-specific block for form-recording
actions) before it is extracted into a standalone ``_build_action_detail_lines``
helper. They exercise the public entry point end-to-end (rather than a not-yet-existing
helper) via the OpenAI-style ``tool_calls`` parsing path used in production
(``evaluation/eval_n1.py`` appends ``message.model_dump(...)`` messages in this shape),
so a refactor of the inline chain can be verified as behavior-preserving.
"""

import json
import re

from evaluation.vis import (
    generate_visualization_html,
    _get_action_marker_style,
    _render_response_section,
    _render_section,
)


def _messages_with_action(action_args: dict, name: str = "left_click") -> list[dict]:
    return [
        {"role": "user", "content": [{"type": "text", "text": "do the task"}]},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": name, "arguments": json.dumps(action_args)}}
            ],
        },
    ]


def _render_action_details(action_args: dict, name: str = "left_click") -> str:
    """Render one action and return the text of its ``action-details`` div."""
    html = generate_visualization_html("task1", _messages_with_action(action_args, name), None)
    match = re.search(r'<div class="action-details">(.*?)</div>', html)
    assert match is not None, html
    return match.group(1)


def _render_action_card(action_args: dict, name: str = "left_click") -> tuple[str, str]:
    """Render one action and return (css_class, action-type label text)."""
    html = generate_visualization_html("task1", _messages_with_action(action_args, name), None)
    match = re.search(r'<div class="(action-item[^"]*)">\s*<div class="action-type">([^<]*)</div>', html)
    assert match is not None, html
    return match.group(1), match.group(2)


class TestActionDetailFields:
    def test_no_recognized_fields_renders_placeholder(self):
        assert _render_action_details({}, name="wait") == "No additional details"

    def test_ref_field(self):
        assert _render_action_details({"ref": "e1"}) == "ref: e1"

    def test_coordinates_field(self):
        assert _render_action_details({"coordinates": [10, 20]}) == "coords: (10, 20)"

    def test_center_coordinates_legacy_field(self):
        assert _render_action_details({"center_coordinates": [5, 6]}) == "coords: (5, 6)"

    def test_coordinates_takes_precedence_over_center_coordinates(self):
        # `coordinates` and `center_coordinates` are checked via if/elif, so when both are
        # present only `coordinates` is used.
        assert _render_action_details({"coordinates": [1, 2], "center_coordinates": [9, 9]}) == "coords: (1, 2)"

    def test_start_coordinates_combines_with_coordinates(self):
        # start_coordinates uses a separate `if`, so it can appear alongside coords.
        result = _render_action_details({"start_coordinates": [1, 2], "coordinates": [3, 4]}, name="drag")
        assert result == "coords: (3, 4), start: (1, 2)"

    def test_text_field(self):
        assert _render_action_details({"text": 'hello "world"'}, name="type") == 'text: "hello "world""'

    def test_direction_and_amount_fields(self):
        result = _render_action_details({"direction": "down", "amount": 3}, name="scroll")
        assert result == "direction: down, amount: 3"

    def test_key_comb_field(self):
        assert _render_action_details({"key_comb": "Control+A"}, name="key_press") == "key: Control+A"

    def test_url_field(self):
        assert _render_action_details({"url": "https://x.com"}, name="goto_url") == "url: https://x.com"

    def test_press_enter_after_and_clear_before_typing_fields(self):
        result = _render_action_details({"press_enter_after": True, "clear_before_typing": False}, name="type")
        assert result == "press_enter_after: True, clear_before_typing: False"

    def test_duration_field(self):
        assert _render_action_details({"duration": 5}, name="wait") == "duration: 5s"

    def test_value_field(self):
        assert _render_action_details({"value": "abc"}, name="select") == 'value: "abc"'


class TestFormActionDetailFields:
    def test_add_question(self):
        result = _render_action_details({"index": 0, "question": "Q?", "response_type": "text"}, name="add_question")
        assert result == 'index: 0, question: "Q?", response_type: text'

    def test_add_input_options(self):
        result = _render_action_details({"question_index": 0, "input_options": "a,b"}, name="add_input_options")
        assert result == 'question_index: 0, input_options: "a,b"'

    def test_add_choices_legacy(self):
        result = _render_action_details({"question_index": 0, "choices": "a,b"}, name="add_choices")
        assert result == 'question_index: 0, choices: "a,b"'

    def test_list_records(self):
        assert _render_action_details({}, name="list_records") == "(outputs all recorded questions)"


class TestActionCardStyling:
    def test_regular_action_uses_plain_css_class_and_label(self):
        css_class, label = _render_action_card({"ref": "e1"}, name="left_click")
        assert css_class == "action-item"
        assert label == "1. left_click"

    def test_form_action_uses_form_css_class_and_icon_label(self):
        css_class, label = _render_action_card(
            {"index": 0, "question": "Q?", "response_type": "text"}, name="add_question"
        )
        assert css_class == "action-item form-action"
        assert label == "📝 1. add_question"


class TestGetActionMarkerStyle:
    """Characterization tests for ``_get_action_marker_style``, pinning its current
    coordinate-field fallback behavior (new ``coordinates`` field preferred over legacy
    ``center_coordinates``, plus a third ``end_coordinates`` fallback and a ``[0, 0]``
    default for drag end-points) before extracting a shared coordinate-lookup helper.
    Uses ``coord_space_width=100, coord_space_height=200`` so the resulting percentages
    are easy to hand-check.
    """

    def test_no_coordinates_and_no_ref(self):
        result = _get_action_marker_style({"action_type": "wait"}, 100, 200)
        assert result == {"type": "wait", "has_point": False, "has_ref_only": False}

    def test_ref_only_carries_ref_through_without_a_point(self):
        result = _get_action_marker_style({"action_type": "left_click", "ref": "e1"}, 100, 200)
        assert result == {"type": "left_click", "ref": "e1", "has_point": False, "has_ref_only": True}

    def test_coordinates_field(self):
        result = _get_action_marker_style({"action_type": "left_click", "coordinates": [50, 100]}, 100, 200)
        assert result == {"type": "left_click", "x": 50.0, "y": 50.0, "has_point": True}

    def test_center_coordinates_legacy_field(self):
        result = _get_action_marker_style({"action_type": "left_click", "center_coordinates": [25, 50]}, 100, 200)
        assert result == {"type": "left_click", "x": 25.0, "y": 25.0, "has_point": True}

    def test_coordinates_takes_precedence_over_center_coordinates(self):
        action = {"action_type": "left_click", "coordinates": [10, 20], "center_coordinates": [90, 90]}
        result = _get_action_marker_style(action, 100, 200)
        assert result["x"] == 10.0
        assert result["y"] == 10.0

    def test_drag_uses_start_coordinates_and_coordinates_as_end(self):
        action = {"action_type": "drag", "start_coordinates": [0, 0], "coordinates": [100, 200]}
        result = _get_action_marker_style(action, 100, 200)
        assert result == {
            "type": "drag",
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 100.0,
            "end_y": 100.0,
            "has_drag": True,
        }

    def test_left_click_drag_action_type_also_treated_as_drag(self):
        action = {"action_type": "left_click_drag", "start_coordinates": [0, 0], "center_coordinates": [50, 100]}
        result = _get_action_marker_style(action, 100, 200)
        assert result["has_drag"] is True
        assert result["end_x"] == 50.0

    def test_drag_falls_back_to_end_coordinates_when_no_coordinates_or_center_coordinates(self):
        action = {"action_type": "drag", "start_coordinates": [0, 0], "end_coordinates": [100, 200]}
        result = _get_action_marker_style(action, 100, 200)
        assert result["end_x"] == 100.0
        assert result["end_y"] == 100.0

    def test_drag_defaults_end_to_zero_when_no_end_coordinate_field_present(self):
        action = {"action_type": "drag", "start_coordinates": [50, 100]}
        result = _get_action_marker_style(action, 100, 200)
        assert result["end_x"] == 0.0
        assert result["end_y"] == 0.0

    def test_start_coordinates_without_drag_action_type_is_not_treated_as_drag(self):
        # The drag branch also requires action_type to be "drag"/"left_click_drag";
        # a plain click with a stray start_coordinates field falls through to the
        # regular point branch instead.
        action = {"action_type": "left_click", "start_coordinates": [1, 2], "coordinates": [3, 4]}
        result = _get_action_marker_style(action, 100, 200)
        assert result.get("has_drag") is None
        assert result["has_point"] is True
        assert result["x"] == 3.0


class TestRenderSection:
    """Characterization tests for ``_render_section``, the shared helper behind the top-level
    collapsible System Prompt / User Query / Evaluation Result blocks in
    ``generate_visualization_html``. Pins the exact markup (including the toggle-on-click
    handler and the collapsed-class placement) so the three call sites stay byte-identical
    to the pre-extraction inline templates.
    """

    def test_not_collapsed_by_default(self):
        html = _render_section("💬 User Query", "hello")
        assert html == (
            "\n"
            '        <div class="section">\n'
            '            <div class="section-header" onclick="this.parentElement.classList.toggle(\'collapsed\')">\n'
            "                <h2>💬 User Query</h2>\n"
            '                <span class="chevron">▼</span>\n'
            "            </div>\n"
            '            <div class="section-content">\n'
            "                <pre>hello</pre>\n"
            "            </div>\n"
            "        </div>\n"
        )

    def test_collapsed(self):
        html = _render_section("🔧 System Prompt", "sys", collapsed=True)
        assert '<div class="section collapsed">' in html
        assert "<h2>🔧 System Prompt</h2>" in html

    def test_escapes_text(self):
        html = _render_section("Title", "<script>alert(1)</script>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestRenderResponseSection:
    """Characterization tests for ``_render_response_section``, the shared helper behind the
    per-step collapsible Actions / Text Observations / Raw Response blocks.
    """

    def test_not_collapsed_by_default(self):
        html = _render_response_section("Actions (2)", "<div>content</div>")
        assert html == (
            '<div class="response-section">\n'
            '                        <div class="response-section-header" '
            "onclick=\"this.parentElement.classList.toggle('collapsed')\">\n"
            "                            <span>▼</span> Actions (2)\n"
            "                        </div>\n"
            '                        <div class="response-section-content">\n'
            "                            <div>content</div>\n"
            "                        </div>\n"
            "                    </div>"
        )

    def test_collapsed(self):
        html = _render_response_section("Raw Response", "<pre>hi</pre>", collapsed=True)
        assert '<div class="response-section collapsed">' in html
        assert "<span>▼</span> Raw Response" in html


class TestTopLevelSectionsEndToEnd:
    """Confirms ``generate_visualization_html`` wires ``_render_section`` for the System
    Prompt (collapsed by default), User Query, and Evaluation Result sections in the
    right order and with the right collapsed state.
    """

    def test_system_prompt_and_user_query_and_result(self):
        from pydantic import BaseModel

        class _Result(BaseModel):
            score: float = 1.0

        messages = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": [{"type": "text", "text": "do the task"}]},
        ]
        html = generate_visualization_html("task1", messages, _Result())

        assert '<div class="section collapsed">\n            <div class="section-header"' in html
        assert "<h2>🔧 System Prompt</h2>" in html
        assert "<h2>💬 User Query</h2>" in html
        assert "<h2>📋 Evaluation Result</h2>" in html
        # User Query and Evaluation Result sections are not collapsed by default.
        assert re.search(r'<div class="section">\s*<div class="section-header"[^>]*>\s*<h2>💬 User Query', html)
        assert re.search(r'<div class="section">\s*<div class="section-header"[^>]*>\s*<h2>📋 Evaluation Result', html)

    def test_no_system_prompt_omits_section(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "do the task"}]}]
        html = generate_visualization_html("task1", messages, None)
        assert "System Prompt" not in html
