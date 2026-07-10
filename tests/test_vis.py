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

from evaluation.vis import generate_visualization_html


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
