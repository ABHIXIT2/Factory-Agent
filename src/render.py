"""Output rendering: confirmation summaries and closing messages for write tools.

Pure formatters — no LLM, no session state, no database access.
Uses Jinja2 templates from messages.yaml via src.messages module.
"""

import json
import logging
from typing import Any

from src import pending
from src.messages import render_tool, render

logger = logging.getLogger(__name__)


# Confirmation summary formatters

def _extract_customer_names(messages: list[dict[str, Any]]) -> dict[int, str]:
    """Walk the message history and build a {customer_id: shop_name} map from
    every search_customer and create_customer tool result. Used to render
    confirmation cards with human-readable names instead of raw IDs."""
    name_map: dict[int, str] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tool_name = msg.get("name")
        try:
            payload = json.loads(msg.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if tool_name == "search_customer":
            for row in payload.get("results", []) or []:
                cid, name = row.get("id"), row.get("shop_name")
                if cid is not None and name:
                    name_map[int(cid)] = name
        elif tool_name == "create_customer" and payload.get("ok"):
            cid = payload.get("customer_id")
            name = payload.get("shop_name")
            if cid is not None and name:
                name_map[int(cid)] = name
    return name_map


def _customer_label(args: dict[str, Any], names: dict[int, str]) -> str:
    """Resolve a customer_id arg to a Markdown-bold shop name; fall back to plain ID."""
    cid = args.get("customer_id")
    try:
        cid_int = int(cid) if cid is not None else None
    except (TypeError, ValueError):
        cid_int = None
    name = names.get(cid_int) if cid_int is not None else None
    return f"*{name}*" if name else f"`{cid}`"




def _build_summary(
    write_calls: list[pending.PendingToolCall],
    customer_names: dict[int, str] | None = None,
    user_lang: str = "hi-Hind",
) -> str:
    """Build confirmation card summary via Jinja2 templates."""
    names = customer_names or {}
    parts = []
    for call in write_calls:
        try:
            summary_text = render_tool(
                call.name, "confirmation", user_lang,
                customer_label=_customer_label(call.arguments, names),
                **call.arguments
            )
            parts.append(summary_text)
        except KeyError as e:
            logger.warning("render_tool failed for %s: %s", call.name, e)
            parts.append(f"📋 {call.name}: {call.arguments}")

    preamble = render("confirmation_preamble", None, user_lang)
    parts.append(f"\n{preamble}")
    return "\n\n".join(parts)


# Closing messages after confirmation

def _parse_tool_result(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content or "{}")
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _customer_text(args: dict[str, Any], names: dict[int, str]) -> str:
    cid = args.get("customer_id")
    try:
        cid_int = int(cid) if cid is not None else None
    except (TypeError, ValueError):
        cid_int = None
    name = names.get(cid_int) if cid_int is not None else None
    return name or f"Customer {cid}"




def _render_closing(
    call: "pending.PendingToolCall",
    tool_result: str,
    customer_names: dict[int, str],
    user_lang: str,
) -> str:
    """Render closing message via Jinja2 or error template."""
    parsed = _parse_tool_result(tool_result)

    if not parsed.get("ok", True):
        err_key = parsed.get("error", "generic")
        try:
            return render("errors", err_key, user_lang,
                         detail=parsed.get("detail", ""),
                         error=err_key)
        except KeyError:
            logger.warning("error template not found for %s, using generic", err_key)
            return render("errors", "generic", user_lang,
                         detail=parsed.get("detail", ""),
                         error="generic")

    try:
        # Merge arguments and result; result wins on conflicts
        template_vars = {**(call.arguments or {}), **parsed}
        return render_tool(
            call.name, "closing", user_lang,
            customer_label=_customer_label(call.arguments, customer_names),
            **template_vars
        )
    except KeyError as e:
        logger.warning("closing template not found for %s: %s", call.name, e)
        return "✅ Saved."
    except Exception as e:
        logger.exception("closing-template render failed for %s: %s", call.name, e)
        return "✅ Saved."


