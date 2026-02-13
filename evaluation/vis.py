import json


def generate_visualization_html(
    task_id: str,
    messages: list[dict],
    result: object | None,
    coord_space_width: int = 1000,
    coord_space_height: int = 1000,
) -> str:
    """Generate a static HTML file for visualizing the evaluation messages and result."""

    def _escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def _escape_json_for_script_tag(json_str: str) -> str:
        """Escape JSON string for safe embedding in HTML script tags.

        This prevents breaking out of script tags and avoids JavaScript parsing issues.
        """
        # Escape </script> and similar patterns that could break out of script tags
        result = json_str.replace("</", "<\\/")
        # Escape HTML comment patterns
        result = result.replace("<!--", "<\\!--")
        # Escape Unicode line/paragraph separators (valid in JSON but can cause issues in JS)
        result = result.replace("\u2028", "\\u2028")
        result = result.replace("\u2029", "\\u2029")
        return result

    def _parse_tool_calls_from_openai_format(tool_calls: list[dict]) -> list[dict]:
        """Parse tool calls from OpenAI-style format (used in eval_forms_baseten_tools.py).

        Each tool_call has format:
        {"id": "...", "function": {"name": "...", "arguments": "{...}"}, "type": "function"}

        Returns a list of dicts with 'action_type' and other parameters extracted from arguments.
        """
        actions = []
        for tc in tool_calls:
            try:
                func = tc.get("function", {})
                name = func.get("name", "unknown")
                arguments_str = func.get("arguments", "{}")
                if isinstance(arguments_str, str):
                    arguments = json.loads(arguments_str)
                else:
                    arguments = arguments_str if arguments_str else {}

                action = {"action_type": name}
                action.update(arguments)
                actions.append(action)
            except (json.JSONDecodeError, TypeError):
                continue

        return actions

    def _parse_tool_calls(msg: dict) -> list[dict]:
        """Parse tool calls from an assistant message in OpenAI format.

        Returns a list of dicts with 'action_type' and other parameters extracted from arguments.
        """
        if "tool_calls" in msg and msg["tool_calls"]:
            return _parse_tool_calls_from_openai_format(msg["tool_calls"])
        return []

    def _get_action_marker_style(action: dict, coord_space_width: int = 1000, coord_space_height: int = 1000) -> dict:
        """Generate CSS positioning for action markers.

        Coordinates can be in different scales:
        - 0-1000 normalized scale (legacy Yutori format)
        - Pixel coordinates (Anthropic format, e.g., 1280x800)

        The coord_space_width/height should match the model's coordinate prediction space.
        """
        action_type = action.get("action_type", "unknown")
        result = {"type": action_type}

        # Carry ref through for display even if no coordinates
        if "ref" in action:
            result["ref"] = action["ref"]

        # Handle coordinate-based actions
        # Check drag first since drags have both start_coordinates and coordinates
        if "start_coordinates" in action and action_type.lower() in ("drag", "left_click_drag"):
            sx, sy = action["start_coordinates"]
            ex, ey = action.get("coordinates", action.get("center_coordinates", action.get("end_coordinates", [0, 0])))
            result["start_x"] = sx / coord_space_width * 100
            result["start_y"] = sy / coord_space_height * 100
            result["end_x"] = ex / coord_space_width * 100
            result["end_y"] = ey / coord_space_height * 100
            result["has_drag"] = True
        elif "coordinates" in action:
            x, y = action["coordinates"]
            result["x"] = x / coord_space_width * 100  # percentage
            result["y"] = y / coord_space_height * 100
            result["has_point"] = True
        elif "center_coordinates" in action:
            x, y = action["center_coordinates"]
            result["x"] = x / coord_space_width * 100
            result["y"] = y / coord_space_height * 100
            result["has_point"] = True
        else:
            result["has_point"] = False
            result["has_ref_only"] = "ref" in action

        return result

    # Build step data
    steps = []
    system_prompt = None
    user_query = None
    current_observation = None
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            system_prompt = content if isinstance(content, str) else json.dumps(content, indent=2)
        elif role == "user":
            if isinstance(content, list):
                # Check for user query (text content) - only set once (first user message)
                if user_query is None:
                    user_query = next((c.get("text", "") for c in content if c.get("type") == "text"), None)

                # Check for Anthropic tool_result format (contains screenshots for observations)
                # Extract images from tool_result content and standalone image blocks
                observation_images = []
                observation_texts = []
                for c in content:
                    c_type = c.get("type")
                    if c_type == "tool_result":
                        # Anthropic tool_result format
                        tool_content = c.get("content", [])
                        if isinstance(tool_content, list):
                            for tc in tool_content:
                                if isinstance(tc, dict):
                                    if tc.get("type") == "image":
                                        # Anthropic base64 image
                                        source = tc.get("source", {})
                                        if source.get("type") == "base64":
                                            data = source.get("data", "")
                                            media_type = source.get("media_type", "image/png")
                                            observation_images.append(f"data:{media_type};base64,{data}")
                                    elif tc.get("type") == "text":
                                        observation_texts.append(tc.get("text", ""))
                    elif c_type == "image":
                        # Standalone Anthropic image block
                        source = c.get("source", {})
                        if source.get("type") == "base64":
                            data = source.get("data", "")
                            media_type = source.get("media_type", "image/png")
                            observation_images.append(f"data:{media_type};base64,{data}")
                    elif c_type == "image_url":
                        # OpenAI format
                        observation_images.append(c.get("image_url", {}).get("url", ""))

                if observation_images or observation_texts:
                    # Build observation content
                    obs_content = []
                    for img_url in observation_images:
                        obs_content.append({"type": "image_url", "image_url": {"url": img_url}})
                    for txt in observation_texts:
                        obs_content.append({"type": "text", "text": txt})
                    current_observation = obs_content
            else:
                if user_query is None:
                    user_query = content
        elif role == "observation":
            current_observation = content if isinstance(content, list) else [content]
        elif role == "tool":
            # Tool role contains observations (screenshots, text results)
            current_observation = content if isinstance(content, list) else [content]
        elif role == "assistant":
            # Pair with the previous observation
            actions = _parse_tool_calls(msg)
            action_markers = [_get_action_marker_style(a, coord_space_width, coord_space_height) for a in actions]

            # Find the screenshot in the observation
            screenshot_url = None
            text_observations = []
            if current_observation:
                for obs in current_observation:
                    if obs.get("type") == "image_url":
                        screenshot_url = obs.get("image_url", {}).get("url", "")
                    elif obs.get("type") == "text":
                        text_observations.append(obs.get("text", ""))

            # Extract content - handle string, list (Anthropic), and dict formats
            assistant_content = content
            if assistant_content is None and "tool_calls" in msg:
                # OpenAI format may have None content with tool_calls
                assistant_content = ""

            # Extract text from Anthropic format (list with text blocks)
            text_parts = []
            if isinstance(assistant_content, list):
                for block in assistant_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif hasattr(block, "type") and block.type == "text":
                        text_parts.append(getattr(block, "text", ""))
                assistant_text = "\n\n".join(text_parts) if text_parts else ""
            elif isinstance(assistant_content, str):
                assistant_text = assistant_content
            else:
                assistant_text = ""

            # If no tool calls, treat the content as a stop/final answer
            is_final_answer = len(actions) == 0
            final_answer_content = None
            if is_final_answer and assistant_text:
                final_answer_content = assistant_text.strip()

            # Format the assistant response for display
            if isinstance(assistant_content, str):
                display_response = assistant_content
            elif isinstance(assistant_content, list):
                # Anthropic format - show text and summarize tool uses
                display_parts = []
                if assistant_text:
                    display_parts.append(assistant_text)
                tool_uses = [
                    b
                    for b in assistant_content
                    if (isinstance(b, dict) and b.get("type") == "tool_use") or getattr(b, "type", None) == "tool_use"
                ]
                if tool_uses:
                    tool_summary = []
                    for tu in tool_uses:
                        name = tu.get("name") if isinstance(tu, dict) else getattr(tu, "name", "unknown")
                        inp = tu.get("input", {}) if isinstance(tu, dict) else getattr(tu, "input", {})
                        # Unwrap browser/computer tool for display
                        if name in ("browser", "computer") and isinstance(inp, dict) and "action" in inp:
                            action_name = inp["action"]
                            # Build a concise summary of the action parameters
                            params = {k: v for k, v in inp.items() if k != "action"}
                            if params:
                                param_parts = []
                                for k, v in params.items():
                                    param_parts.append(f"{k}={json.dumps(v)}")
                                tool_summary.append(f"{action_name}({', '.join(param_parts)})")
                            else:
                                tool_summary.append(f"{action_name}()")
                        else:
                            tool_summary.append(f"{name}({json.dumps(inp)})")
                    display_parts.append("Tool calls:\n" + "\n".join(tool_summary))
                if display_parts:
                    display_response = "\n\n".join(display_parts)
                else:
                    display_response = json.dumps(assistant_content, indent=2)
            else:
                display_response = json.dumps(assistant_content, indent=2) if assistant_content else ""

            # If OpenAI format with tool_calls, show a more readable format
            if "tool_calls" in msg and msg["tool_calls"]:
                tool_calls_summary = []
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    tool_calls_summary.append(f"{func.get('name', 'unknown')}({func.get('arguments', '{}')})")
                if assistant_text:
                    display_response = f"{assistant_text}\n\nTool calls:\n" + "\n".join(tool_calls_summary)
                else:
                    display_response = "Tool calls:\n" + "\n".join(tool_calls_summary)

            steps.append(
                {
                    "step_num": len(steps) + 1,
                    "screenshot_url": screenshot_url,
                    "text_observations": text_observations,
                    "assistant_response": display_response,
                    "actions": actions,
                    "action_markers": action_markers,
                    "is_final_answer": is_final_answer,
                    "final_answer_content": final_answer_content,
                }
            )
            current_observation = None

    # Generate HTML
    result_score = getattr(result, "score", None) if result else None
    result_json = (
        json.dumps(result.model_dump(mode="json"), indent=2) if result and hasattr(result, "model_dump") else None
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Eval: {_escape_html(task_id)}</title>
    <style>
        :root {{
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --border-color: #30363d;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --accent-blue: #58a6ff;
            --accent-green: #3fb950;
            --accent-red: #f85149;
            --accent-yellow: #d29922;
            --accent-purple: #a371f7;
            --accent-orange: #f0883e;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            padding: 2rem;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        header {{
            margin-bottom: 2rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--border-color);
        }}

        h1 {{
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--accent-blue);
            margin-bottom: 0.5rem;
        }}

        .task-id {{
            font-size: 0.875rem;
            color: var(--text-secondary);
        }}

        .result-badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 2rem;
            font-size: 0.875rem;
            font-weight: 600;
            margin-top: 0.5rem;
        }}

        .result-badge.success {{
            background: rgba(63, 185, 80, 0.15);
            color: var(--accent-green);
            border: 1px solid var(--accent-green);
        }}

        .result-badge.failure {{
            background: rgba(248, 81, 73, 0.15);
            color: var(--accent-red);
            border: 1px solid var(--accent-red);
        }}

        .result-badge.partial {{
            background: rgba(210, 153, 34, 0.15);
            color: var(--accent-yellow);
            border: 1px solid var(--accent-yellow);
        }}

        .section {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            margin-bottom: 1.5rem;
            overflow: hidden;
        }}

        .section-header {{
            padding: 1rem 1.25rem;
            background: var(--bg-tertiary);
            border-bottom: 1px solid var(--border-color);
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 0.75rem;
            user-select: none;
        }}

        .section-header:hover {{
            background: #282e36;
        }}

        .section-header h2 {{
            font-size: 0.9rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
        }}

        .section-header .chevron {{
            margin-left: auto;
            transition: transform 0.2s;
        }}

        .section.collapsed .chevron {{
            transform: rotate(-90deg);
        }}

        .section.collapsed .section-content {{
            display: none;
        }}

        .section-content {{
            padding: 1.25rem;
        }}

        pre {{
            background: var(--bg-primary);
            padding: 1rem;
            border-radius: 6px;
            overflow-x: auto;
            font-size: 0.8rem;
            white-space: pre-wrap;
            word-break: break-word;
        }}

        .step {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            margin-bottom: 1.5rem;
            overflow: hidden;
        }}

        .step-header {{
            padding: 1rem 1.25rem;
            background: linear-gradient(135deg, var(--bg-tertiary) 0%, var(--bg-secondary) 100%);
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            gap: 1rem;
        }}

        .step-number {{
            width: 2rem;
            height: 2rem;
            background: var(--accent-blue);
            color: var(--bg-primary);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 0.875rem;
        }}

        .step-title {{
            font-weight: 600;
        }}

        .step-content {{
            display: grid;
            grid-template-columns: 3fr 2fr;
            gap: 1.5rem;
            padding: 1.25rem;
            align-items: start;
        }}

        @media (max-width: 1200px) {{
            .step-content {{
                grid-template-columns: 1fr;
            }}
        }}

        .screenshot-container {{
            background: var(--bg-primary);
            border-radius: 6px;
            overflow: visible;
            border: 1px solid var(--border-color);
            display: flex;
            justify-content: center;
            align-items: flex-start;
            padding: 8px;
        }}

        .screenshot-wrapper {{
            position: relative;
            display: inline-block;
            line-height: 0;
            cursor: zoom-in;
            border-radius: 4px;
            overflow: visible;
        }}

        .screenshot-wrapper img {{
            max-width: 100%;
            height: auto;
            display: block;
            border-radius: 4px;
        }}

        .action-marker {{
            position: absolute;
            transform: translate(-50%, -50%);
            z-index: 10;
            pointer-events: none;
        }}

        .action-point {{
            width: 24px;
            height: 24px;
            border-radius: 50%;
            background: var(--accent-red);
            border: 3px solid white;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
            animation: pulse 1.5s ease-in-out infinite;
        }}

        .action-point.click {{
            background: var(--accent-red);
        }}

        .action-point.scroll {{
            background: var(--accent-blue);
        }}

        .action-point.type {{
            background: var(--accent-green);
        }}

        .action-point.hover {{
            background: var(--accent-purple);
        }}

        .action-ref-badge {{
            position: absolute;
            top: 8px;
            right: 8px;
            background: rgba(88, 166, 255, 0.9);
            color: white;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            pointer-events: none;
            z-index: 10;
            display: flex;
            flex-direction: column;
            gap: 4px;
            max-width: 200px;
        }}

        .action-ref-badge .ref-item {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .action-ref-badge .ref-action-type {{
            font-size: 0.65rem;
            opacity: 0.85;
            text-transform: uppercase;
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: 0.8; transform: scale(1.2); }}
        }}

        .action-label {{
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            margin-top: 6px;
            background: rgba(0, 0, 0, 0.5);
            color: white;
            padding: 10px 10px;
            border-radius: 6px;
            font-size: 0.7rem;
            white-space: nowrap;
            font-weight: 600;
        }}

        .drag-line {{
            position: absolute;
            pointer-events: none;
            z-index: 9;
        }}

        .response-panel {{
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }}

        .response-section {{
            background: var(--bg-primary);
            border-radius: 6px;
            overflow: hidden;
        }}

        .response-section-header {{
            padding: 0.5rem 0.75rem;
            background: var(--bg-tertiary);
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        .response-section-header:hover {{
            background: #282e36;
        }}

        .response-section-content {{
            padding: 0.75rem;
            max-height: 400px;
            overflow-y: auto;
        }}

        .response-section.collapsed .response-section-content {{
            display: none;
        }}

        .action-list {{
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }}

        .action-item {{
            background: var(--bg-secondary);
            padding: 0.75rem;
            border-radius: 4px;
            border-left: 3px solid var(--accent-blue);
        }}

        .action-type {{
            font-weight: 600;
            color: var(--accent-blue);
            margin-bottom: 0.25rem;
        }}

        .action-details {{
            font-size: 0.8rem;
            color: var(--text-secondary);
        }}

        .legend {{
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            padding: 0.75rem 1rem;
            background: var(--bg-tertiary);
            border-top: 1px solid var(--border-color);
            font-size: 0.75rem;
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        .legend-dot {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            border: 2px solid white;
        }}

        .nav-buttons {{
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            display: flex;
            gap: 0.5rem;
            z-index: 100;
        }}

        .nav-btn {{
            padding: 0.75rem 1.25rem;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-primary);
            cursor: pointer;
            font-family: inherit;
            font-size: 0.875rem;
            transition: all 0.2s;
        }}

        .nav-btn:hover {{
            background: var(--accent-blue);
            border-color: var(--accent-blue);
        }}

        .text-observation {{
            background: var(--bg-tertiary);
            padding: 0.75rem;
            border-radius: 4px;
            font-size: 0.8rem;
            max-height: 200px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-word;
        }}

        /* Modal / Lightbox */
        .modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.92);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 2rem;
            cursor: zoom-out;
        }}

        .modal-overlay.active {{
            display: flex;
        }}

        .modal-content {{
            position: relative;
            max-width: 95vw;
            max-height: 95vh;
            display: inline-block;
            line-height: 0;
            cursor: default;
        }}

        .modal-content img {{
            max-width: 95vw;
            max-height: 95vh;
            width: auto;
            height: auto;
            object-fit: contain;
            display: block;
            border-radius: 4px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
        }}

        .modal-content .drag-line {{
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
        }}

        .modal-content .action-marker {{
            pointer-events: none;
        }}

        .modal-content .action-point {{
            width: 32px;
            height: 32px;
            border-width: 4px;
        }}

        .modal-content .action-label {{
            font-size: 0.85rem;
            padding: 12px 12px;
        }}

        .modal-close {{
            position: fixed;
            top: 1.5rem;
            right: 1.5rem;
            width: 48px;
            height: 48px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 50%;
            color: var(--text-primary);
            font-size: 1.5rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            z-index: 1001;
        }}

        .modal-close:hover {{
            background: var(--accent-red);
            border-color: var(--accent-red);
        }}

        .modal-step-info {{
            position: fixed;
            bottom: 1.5rem;
            left: 50%;
            transform: translateX(-50%);
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 0.5rem 1rem;
            font-size: 0.875rem;
            color: var(--text-secondary);
            z-index: 1001;
        }}

        .modal-nav {{
            position: fixed;
            top: 50%;
            transform: translateY(-50%);
            width: 48px;
            height: 48px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 50%;
            color: var(--text-primary);
            font-size: 1.25rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            z-index: 1001;
        }}

        .modal-nav:hover {{
            background: var(--accent-blue);
            border-color: var(--accent-blue);
        }}

        .modal-nav.prev {{
            left: 1.5rem;
        }}

        .modal-nav.next {{
            right: 1.5rem;
        }}

        .click-hint {{
            position: absolute;
            bottom: 8px;
            right: 8px;
            background: rgba(0, 0, 0, 0.7);
            color: var(--text-secondary);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.7rem;
            pointer-events: none;
        }}

        /* Stop action styling */
        .action-item.stop-action {{
            cursor: pointer;
            border-left-color: var(--accent-green);
            transition: all 0.2s;
        }}

        .action-item.stop-action:hover {{
            background: var(--bg-tertiary);
            transform: translateX(4px);
        }}

        .action-item.stop-action .action-type {{
            color: var(--accent-green);
        }}

        .action-item.stop-action .click-to-expand {{
            font-size: 0.7rem;
            color: var(--text-secondary);
            margin-top: 4px;
            font-style: italic;
        }}

        /* Form recording action styling */
        .action-item.form-action {{
            border-left-color: #c792ea;  /* Light purple for form actions */
        }}

        .action-item.form-action .action-type {{
            color: #c792ea;
        }}

        .action-item.form-action .action-details {{
            font-family: 'SF Mono', 'Fira Code', monospace;
        }}

        /* Answer Modal */
        .answer-modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.92);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 2rem;
        }}

        .answer-modal-overlay.active {{
            display: flex;
        }}

        .answer-modal-content {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            max-width: 900px;
            width: 100%;
            max-height: 85vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }}

        .answer-modal-header {{
            padding: 1.25rem 1.5rem;
            background: var(--bg-tertiary);
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .answer-modal-header h3 {{
            font-size: 1rem;
            font-weight: 600;
            color: var(--accent-green);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        .answer-modal-close {{
            width: 32px;
            height: 32px;
            background: transparent;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-secondary);
            font-size: 1.25rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
        }}

        .answer-modal-close:hover {{
            background: var(--accent-red);
            border-color: var(--accent-red);
            color: white;
        }}

        .answer-modal-body {{
            padding: 1.5rem;
            overflow-y: auto;
            flex: 1;
        }}

        /* Markdown rendered content */
        .markdown-content {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            font-size: 0.95rem;
            line-height: 1.7;
            color: var(--text-primary);
        }}

        .markdown-content h1, .markdown-content h2, .markdown-content h3,
        .markdown-content h4, .markdown-content h5, .markdown-content h6 {{
            margin-top: 1.5em;
            margin-bottom: 0.5em;
            font-weight: 600;
            color: var(--text-primary);
        }}

        .markdown-content h1 {{
            font-size: 1.5rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.3em;
        }}
        .markdown-content h2 {{
            font-size: 1.3rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.3em;
        }}
        .markdown-content h3 {{ font-size: 1.15rem; }}
        .markdown-content h4 {{ font-size: 1rem; }}

        .markdown-content p {{
            margin-bottom: 1em;
        }}

        .markdown-content ul, .markdown-content ol {{
            margin-bottom: 1em;
            padding-left: 1.5em;
        }}

        .markdown-content li {{
            margin-bottom: 0.4em;
        }}

        .markdown-content code {{
            background: var(--bg-primary);
            padding: 0.2em 0.4em;
            border-radius: 4px;
            font-family: 'SF Mono', 'Fira Code', monospace;
            font-size: 0.9em;
        }}

        .markdown-content pre {{
            background: var(--bg-primary);
            padding: 1rem;
            border-radius: 6px;
            overflow-x: auto;
            margin-bottom: 1em;
        }}

        .markdown-content pre code {{
            background: none;
            padding: 0;
        }}

        .markdown-content blockquote {{
            border-left: 4px solid var(--accent-blue);
            margin: 1em 0;
            padding: 0.5em 1em;
            background: var(--bg-primary);
            border-radius: 0 6px 6px 0;
        }}

        .markdown-content a {{
            color: var(--accent-blue);
            text-decoration: none;
        }}

        .markdown-content a:hover {{
            text-decoration: underline;
        }}

        .markdown-content table {{
            border-collapse: collapse;
            width: 100%;
            margin-bottom: 1em;
        }}

        .markdown-content th, .markdown-content td {{
            border: 1px solid var(--border-color);
            padding: 0.5em 0.75em;
            text-align: left;
        }}

        .markdown-content th {{
            background: var(--bg-tertiary);
            font-weight: 600;
        }}

        .markdown-content strong {{
            font-weight: 600;
            color: var(--text-primary);
        }}

        .markdown-content em {{
            font-style: italic;
        }}

        .markdown-content hr {{
            border: none;
            border-top: 1px solid var(--border-color);
            margin: 1.5em 0;
        }}
    </style>
    <!-- Marked.js for markdown rendering -->
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Evaluation Visualization</h1>
            <div class="task-id">Task ID: {_escape_html(task_id)}</div>
            {
        "<div class='result-badge "
        + ("success" if result_score == 1.0 else "partial" if result_score and result_score > 0 else "failure")
        + "'>Score: "
        + str(result_score)
        + "</div>"
        if result_score is not None
        else ""
    }
        </header>
"""

    # System prompt section
    if system_prompt:
        html += f"""
        <div class="section collapsed">
            <div class="section-header" onclick="this.parentElement.classList.toggle('collapsed')">
                <h2>🔧 System Prompt</h2>
                <span class="chevron">▼</span>
            </div>
            <div class="section-content">
                <pre>{_escape_html(system_prompt)}</pre>
            </div>
        </div>
"""

    # User query section
    if user_query:
        html += f"""
        <div class="section">
            <div class="section-header" onclick="this.parentElement.classList.toggle('collapsed')">
                <h2>💬 User Query</h2>
                <span class="chevron">▼</span>
            </div>
            <div class="section-content">
                <pre>{_escape_html(user_query)}</pre>
            </div>
        </div>
"""

    # Steps
    for step in steps:
        step_num = step["step_num"]
        screenshot_url = step["screenshot_url"]
        actions = step["actions"]
        action_markers = step["action_markers"]
        assistant_response = step["assistant_response"]
        text_observations = step["text_observations"]

        # Generate action markers HTML
        markers_html = ""
        ref_only_items = []  # Collect ref-only actions for a single badge
        for i, marker in enumerate(action_markers):
            if marker.get("has_point"):
                action_type = marker["type"]
                # Map action types to color classes
                color_class = {
                    "left_click": "click",
                    "double_click": "click",
                    "triple_click": "click",
                    "right_click": "click",
                    "click": "click",  # Legacy support
                    "scroll": "scroll",
                    "type": "type",
                    "hover": "hover",
                }.get(action_type.lower(), "click")
                markers_html += f"""
                <div class="action-marker" style="left: {marker["x"]}%; top: {marker["y"]}%;">
                    <div class="action-point {color_class}"></div>
                    <div class="action-label">{i + 1}. {action_type}</div>
                </div>
"""
            elif marker.get("has_drag"):
                markers_html += f"""
                <svg class="drag-line" style="position: absolute; left: 0; top: 0; width: 100%; height: 100%; pointer-events: none;">
                    <defs>
                        <marker id="arrowhead-{step_num}-{i}" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                            <polygon points="0 0, 10 3.5, 0 7" fill="#f0883e"/>
                        </marker>
                    </defs>
                    <line x1="{marker["start_x"]}%" y1="{marker["start_y"]}%" x2="{marker["end_x"]}%" y2="{marker["end_y"]}%"
                          stroke="#f0883e" stroke-width="3" marker-end="url(#arrowhead-{step_num}-{i})"/>
                </svg>
                <div class="action-marker" style="left: {marker["start_x"]}%; top: {marker["start_y"]}%;">
                    <div class="action-point" style="background: var(--accent-orange);"></div>
                    <div class="action-label">{i + 1}. drag start</div>
                </div>
"""  # noqa: E501
            elif marker.get("has_ref_only") and marker.get("ref"):
                ref_only_items.append((i, marker["type"], marker["ref"]))

        # Render ref-only badge on screenshot (top-right corner)
        if ref_only_items:
            ref_items_html = ""
            for idx, act_type, ref_name in ref_only_items:
                ref_items_html += (
                    f'<div class="ref-item">'
                    f'<span class="ref-action-type">{idx + 1}. {act_type}</span>'
                    f"<span>{_escape_html(ref_name)}</span>"
                    f"</div>"
                )
            markers_html += f"""
                <div class="action-ref-badge">{ref_items_html}</div>
"""

        # Check if this is a final answer step (no tool calls)
        is_final_answer = step.get("is_final_answer", False)
        final_answer_content = step.get("final_answer_content")

        # Generate actions summary HTML
        actions_html = ""

        # Handle final answer case (no tool calls = implicit stop)
        if is_final_answer and final_answer_content:
            answer_preview = (
                final_answer_content[:150] + "..." if len(final_answer_content) > 150 else final_answer_content
            )
            step["stop_answer"] = final_answer_content
            actions_html = f"""
            <div class="action-item stop-action" onclick="openAnswerModal({step_num})">
                <div class="action-type">✅ Final Answer (No Tool Call)</div>
                <div class="action-details">{_escape_html(answer_preview)}</div>
                <div class="click-to-expand">Click to view full answer</div>
            </div>
"""
        else:
            # Check if any action is a stop action (Finished/CallUser)
            stop_actions = [
                a for a in actions if a.get("action_type") in ("Finished", "CallUser", "finished", "call_user")
            ]
            if stop_actions:
                stop_text = stop_actions[0].get("text", "")
                if stop_text:
                    answer_preview = stop_text[:150] + "..." if len(stop_text) > 150 else stop_text
                    step["stop_answer"] = stop_text
                    stop_label = stop_actions[0].get("action_type", "Finished")
                    actions_html = f"""
            <div class="action-item stop-action" onclick="openAnswerModal({step_num})">
                <div class="action-type">✅ {stop_label}</div>
                <div class="action-details">{_escape_html(answer_preview)}</div>
                <div class="click-to-expand">Click to view full answer</div>
            </div>
"""

            for i, action in enumerate(actions):
                action_type = action.get("action_type", "unknown")
                # Skip stop actions already rendered above
                if action_type in ("Finished", "CallUser", "finished", "call_user"):
                    continue
                details = []

                # Handle element reference (browser tool ref-based targeting)
                if "ref" in action:
                    details.append(f"ref: {action['ref']}")
                # Handle coordinates (new format uses "coordinates")
                if "coordinates" in action:
                    coords = action["coordinates"]
                    details.append(f"coords: ({coords[0]}, {coords[1]})")
                elif "center_coordinates" in action:
                    # Legacy support
                    coords = action["center_coordinates"]
                    details.append(f"coords: ({coords[0]}, {coords[1]})")
                if "start_coordinates" in action:
                    coords = action["start_coordinates"]
                    details.append(f"start: ({coords[0]}, {coords[1]})")
                if "text" in action:
                    details.append(f'text: "{action["text"]}"')
                if "direction" in action:
                    details.append(f"direction: {action['direction']}")
                if "amount" in action:
                    details.append(f"amount: {action['amount']}")
                if "key_comb" in action:
                    details.append(f"key: {action['key_comb']}")
                if "url" in action:
                    details.append(f"url: {action['url']}")
                if "press_enter_after" in action:
                    details.append(f"press_enter_after: {action['press_enter_after']}")
                if "clear_before_typing" in action:
                    details.append(f"clear_before_typing: {action['clear_before_typing']}")
                if "duration" in action:
                    details.append(f"duration: {action['duration']}s")
                if "value" in action:
                    details.append(f'value: "{action["value"]}"')

                # Form recording actions: add_question and add_input_options (renamed from add_choices)
                if action_type == "add_question":
                    details.append(f"index: {action.get('index', '?')}")
                    details.append(f'question: "{action.get("question", "")}"')
                    details.append(f"response_type: {action.get('response_type', '?')}")
                if action_type == "add_input_options":
                    details.append(f"question_index: {action.get('question_index', '?')}")
                    details.append(f'input_options: "{action.get("input_options", "")}"')
                if action_type == "add_choices":
                    # Legacy support for old name
                    details.append(f"question_index: {action.get('question_index', '?')}")
                    details.append(f'choices: "{action.get("choices", "")}"')
                if action_type == "list_records":
                    details.append("(outputs all recorded questions)")

                # Special styling for form recording actions
                if action_type in ("add_question", "add_input_options", "add_choices", "list_records"):
                    icon = {
                        "add_question": "📝",
                        "add_input_options": "📋",
                        "add_choices": "📋",  # Legacy
                        "list_records": "📊",
                    }.get(action_type, "")
                    actions_html += f"""
            <div class="action-item form-action">
                <div class="action-type">{icon} {i + 1}. {action_type}</div>
                <div class="action-details">{", ".join(details) if details else "No additional details"}</div>
            </div>
"""
                else:
                    actions_html += f"""
            <div class="action-item">
                <div class="action-type">{i + 1}. {action_type}</div>
                <div class="action-details">{", ".join(details) if details else "No additional details"}</div>
            </div>
"""

        # Text observations HTML
        text_obs_html = ""
        for text_obs in text_observations:
            text_obs_html += f"""
            <div class="text-observation">{_escape_html(text_obs[:2000])}</div>
"""

        html += f"""
        <div class="step" id="step-{step_num}">
            <div class="step-header">
                <div class="step-number">{step_num}</div>
                <div class="step-title">Step {step_num}</div>
            </div>
            <div class="step-content">
                <div class="screenshot-container">
                    {
            f'''<div class="screenshot-wrapper" onclick="openModal({step_num})" data-step="{step_num}">
                        <img src="{screenshot_url}" alt="Screenshot for step {step_num}">
                        {markers_html}
                    </div>'''
            if screenshot_url
            else '<div style="padding: 2rem; color: var(--text-secondary);">No screenshot available</div>'
        }
                </div>
                <div class="response-panel">
                    <div class="response-section">
                        <div class="response-section-header" onclick="this.parentElement.classList.toggle('collapsed')">
                            <span>▼</span> Actions ({len(actions)})
                        </div>
                        <div class="response-section-content">
                            <div class="action-list">
                                {
            actions_html if actions_html else '<div style="color: var(--text-secondary);">No actions</div>'
        }
                            </div>
                        </div>
                    </div>
                    {
            f'''<div class="response-section collapsed">
                        <div class="response-section-header" onclick="this.parentElement.classList.toggle('collapsed')">
                            <span>▼</span> Text Observations
                        </div>
                        <div class="response-section-content">
                            {text_obs_html}
                        </div>
                    </div>'''
            if text_observations
            else ""
        }
                    <div class="response-section collapsed">
                        <div class="response-section-header" onclick="this.parentElement.classList.toggle('collapsed')">
                            <span>▼</span> Raw Response
                        </div>
                        <div class="response-section-content">
                            <pre>{_escape_html(assistant_response)}</pre>
                        </div>
                    </div>
                </div>
            </div>
            <div class="legend">
                <div class="legend-item"><div class="legend-dot" style="background: var(--accent-red);"></div> Click</div>
                <div class="legend-item"><div class="legend-dot" style="background: var(--accent-blue);"></div> Scroll</div>
                <div class="legend-item"><div class="legend-dot" style="background: var(--accent-green);"></div> Type</div>
                <div class="legend-item"><div class="legend-dot" style="background: var(--accent-purple);"></div> Hover</div>
                <div class="legend-item"><div class="legend-dot" style="background: var(--accent-orange);"></div> Drag</div>
            </div>
        </div>
"""  # noqa: E501

    # Result section
    if result_json:
        html += f"""
        <div class="section">
            <div class="section-header" onclick="this.parentElement.classList.toggle('collapsed')">
                <h2>📋 Evaluation Result</h2>
                <span class="chevron">▼</span>
            </div>
            <div class="section-content">
                <pre>{_escape_html(result_json)}</pre>
            </div>
        </div>
"""

    # Build modal data for JavaScript
    modal_steps_data = []
    stop_answers_data = {}
    for step in steps:
        if step["screenshot_url"]:
            modal_steps_data.append(
                {
                    "step_num": step["step_num"],
                    "screenshot_url": step["screenshot_url"],
                    "markers": step["action_markers"],
                }
            )
        if "stop_answer" in step:
            stop_answers_data[step["step_num"]] = step["stop_answer"]

    modal_data_json = _escape_json_for_script_tag(json.dumps(modal_steps_data))
    stop_answers_json = _escape_json_for_script_tag(json.dumps(stop_answers_data))

    # Navigation and closing tags
    html += f"""
        <div class="nav-buttons">
            <button class="nav-btn" onclick="window.scrollTo({{top: 0, behavior: 'smooth'}})">↑ Top</button>
            <button class="nav-btn" onclick="document.getElementById('step-{len(steps)}')?.scrollIntoView({{behavior: 'smooth'}})">↓ Last Step</button>
        </div>

        <!-- Screenshot Modal -->
        <div class="modal-overlay" id="modal" onclick="closeModal(event)">
            <button class="modal-close" onclick="closeModal(event)">×</button>
            <button class="modal-nav prev" onclick="prevModalStep(event)">‹</button>
            <button class="modal-nav next" onclick="nextModalStep(event)">›</button>
            <div class="modal-content" id="modal-content"></div>
            <div class="modal-step-info" id="modal-step-info"></div>
        </div>

        <!-- Answer Modal -->
        <div class="answer-modal-overlay" id="answer-modal" onclick="closeAnswerModal(event)">
            <div class="answer-modal-content" onclick="event.stopPropagation()">
                <div class="answer-modal-header">
                    <h3>✅ Final Answer <span id="answer-step-info"></span></h3>
                    <button class="answer-modal-close" onclick="closeAnswerModal(event)">×</button>
                </div>
                <div class="answer-modal-body">
                    <div class="markdown-content" id="answer-content"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const stepsData = {modal_data_json};
        const stopAnswers = {stop_answers_json};
        let currentModalStep = 0;
        const totalSteps = stepsData.length;

        function getMarkerHtml(marker, index) {{
            if (marker.has_point) {{
                const colorClass = {{
                    'left_click': 'click',
                    'double_click': 'click',
                    'triple_click': 'click',
                    'right_click': 'click',
                    'click': 'click',
                    'scroll': 'scroll',
                    'type': 'type',
                    'hover': 'hover',
                    'longpress': 'click',
                    'pressenter': 'type',
                    'launch': 'scroll'
                }}[marker.type.toLowerCase()] || 'click';
                return `<div class="action-marker" style="left: ${{marker.x}}%; top: ${{marker.y}}%;">
                    <div class="action-point ${{colorClass}}"></div>
                    <div class="action-label">${{index + 1}}. ${{marker.type}}</div>
                </div>`;
            }} else if (marker.has_drag) {{
                return `<svg class="drag-line" style="position: absolute; left: 0; top: 0; width: 100%; height: 100%; pointer-events: none;">
                    <defs>
                        <marker id="modal-arrowhead-${{index}}" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                            <polygon points="0 0, 10 3.5, 0 7" fill="#f0883e"/>
                        </marker>
                    </defs>
                    <line x1="${{marker.start_x}}%" y1="${{marker.start_y}}%" x2="${{marker.end_x}}%" y2="${{marker.end_y}}%"
                          stroke="#f0883e" stroke-width="4" marker-end="url(#modal-arrowhead-${{index}})"/>
                </svg>
                <div class="action-marker" style="left: ${{marker.start_x}}%; top: ${{marker.start_y}}%;">
                    <div class="action-point" style="background: var(--accent-orange);"></div>
                    <div class="action-label">${{index + 1}}. drag start</div>
                </div>`;
            }}
            return '';
        }}

        function getRefBadgeHtml(markers) {{
            // Collect ref-only markers
            const refItems = markers
                .map((m, i) => ({{ marker: m, index: i }}))
                .filter(item => item.marker.has_ref_only && item.marker.ref);
            if (refItems.length === 0) return '';
            const items = refItems.map(item =>
                `<div class="ref-item"><span class="ref-action-type">${{item.index + 1}}. ${{item.marker.type}}</span><span>${{item.marker.ref}}</span></div>`
            ).join('');
            return `<div class="action-ref-badge">${{items}}</div>`;
        }}

        function renderModal(stepIndex) {{
            if (stepIndex < 0 || stepIndex >= totalSteps) return;
            currentModalStep = stepIndex;

            const step = stepsData[stepIndex];
            const markersHtml = step.markers.map((m, i) => getMarkerHtml(m, i)).join('');
            const refBadgeHtml = getRefBadgeHtml(step.markers);

            document.getElementById('modal-content').innerHTML = `
                <img src="${{step.screenshot_url}}" alt="Step ${{step.step_num}}">
                ${{markersHtml}}
                ${{refBadgeHtml}}
            `;
            document.getElementById('modal-step-info').textContent = `Step ${{step.step_num}} of ${{totalSteps}}`;

            // Update nav button visibility
            document.querySelector('.modal-nav.prev').style.display = stepIndex > 0 ? 'flex' : 'none';
            document.querySelector('.modal-nav.next').style.display = stepIndex < totalSteps - 1 ? 'flex' : 'none';
        }}

        function openModal(stepNum) {{
            const stepIndex = stepsData.findIndex(s => s.step_num === stepNum);
            if (stepIndex === -1) return;

            renderModal(stepIndex);
            document.getElementById('modal').classList.add('active');
            document.body.style.overflow = 'hidden';
        }}

        function closeModal(event) {{
            if (event.target.closest('.modal-content') || event.target.closest('.modal-nav')) return;
            document.getElementById('modal').classList.remove('active');
            document.body.style.overflow = '';
        }}

        function prevModalStep(event) {{
            event.stopPropagation();
            if (currentModalStep > 0) {{
                renderModal(currentModalStep - 1);
            }}
        }}

        function nextModalStep(event) {{
            event.stopPropagation();
            if (currentModalStep < totalSteps - 1) {{
                renderModal(currentModalStep + 1);
            }}
        }}

        // Markdown renderer using marked.js with fallback
        function renderMarkdown(text) {{
            // Fallback: escape HTML and convert newlines to <br>
            function fallbackRender(str) {{
                return str
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/\\n/g, '<br>');
            }}

            // Try using marked.js if available
            if (typeof marked !== 'undefined' && marked.parse) {{
                try {{
                    // Configure marked for safe rendering
                    marked.setOptions({{
                        breaks: true,  // Convert \\n to <br>
                        gfm: true      // GitHub Flavored Markdown
                    }});
                    return marked.parse(text);
                }} catch (e) {{
                    console.warn('Markdown parsing failed, using fallback:', e);
                    return fallbackRender(text);
                }}
            }}

            // Fallback if marked is not available
            console.warn('marked.js not loaded, using plain text fallback');
            return fallbackRender(text);
        }}

        function openAnswerModal(stepNum) {{
            const answer = stopAnswers[stepNum];
            if (!answer) return;
            const renderedContent = renderMarkdown(answer);
            document.getElementById('answer-content').innerHTML = renderedContent;
            document.getElementById('answer-step-info').textContent = `(Step ${{stepNum}})`;
            document.getElementById('answer-modal').classList.add('active');
            document.body.style.overflow = 'hidden';
        }}

        function closeAnswerModal(event) {{
            if (
                event &&
                event.target.closest('.answer-modal-content') &&
                !event.target.closest('.answer-modal-close')
            ) {{
                return;
            }}
            document.getElementById('answer-modal').classList.remove('active');
            document.body.style.overflow = '';
        }}

        // Keyboard navigation
        document.addEventListener('keydown', function(e) {{
            const modal = document.getElementById('modal');
            const answerModal = document.getElementById('answer-modal');
            const isModalOpen = modal.classList.contains('active');
            const isAnswerModalOpen = answerModal.classList.contains('active');

            if (isAnswerModalOpen) {{
                if (e.key === 'Escape') {{
                    closeAnswerModal();
                }}
                return;
            }}

            if (isModalOpen) {{
                if (e.key === 'Escape') {{
                    modal.classList.remove('active');
                    document.body.style.overflow = '';
                }} else if (e.key === 'ArrowLeft') {{
                    prevModalStep(e);
                }} else if (e.key === 'ArrowRight') {{
                    nextModalStep(e);
                }}
                return;
            }}

            if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {{
                // Navigate to previous step
                const steps = document.querySelectorAll('.step');
                const scrollY = window.scrollY + 100;
                for (let i = steps.length - 1; i >= 0; i--) {{
                    if (steps[i].offsetTop < scrollY) {{
                        if (i > 0) steps[i - 1].scrollIntoView({{behavior: 'smooth', block: 'start'}});
                        break;
                    }}
                }}
            }} else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {{
                // Navigate to next step
                const steps = document.querySelectorAll('.step');
                const scrollY = window.scrollY + 100;
                for (let i = 0; i < steps.length; i++) {{
                    if (steps[i].offsetTop > scrollY) {{
                        steps[i].scrollIntoView({{behavior: 'smooth', block: 'start'}});
                        break;
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""  # noqa: E501
    return html
