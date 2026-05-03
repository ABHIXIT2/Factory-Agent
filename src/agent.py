"""
Agent loop: Groq LLM with tool-calling and confirm-before-write.

- Sessions stored in a thread-safe TTL cache (bounded memory).
- Per-user rate limiting (sliding window).
- Blocking Groq SDK calls offloaded with asyncio.to_thread.
- Tool results returned in OpenAI's `role: "tool"` format (Groq is OpenAI-compatible).
- Write tools (save_*, record_payment, create_customer) defer execution; the
  loop returns AgentResult(confirmation=...) and bot.py shows inline buttons.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import groq as groq_sdk
import openai

from src.config import (
    GROQ_MODEL, GROQ_MODEL_FAST,
    SYSTEM_PROMPT, TOOLS, MAX_ITERATIONS,
)
from src.providers import call_llm
from src.session import (
    get_history, set_history, clear_history, check_rate_limit,
    inject_selected_customer,
)
from src.tools import execute_tool
from src import pending, selection
from src.render import _build_summary, _render_closing, _extract_customer_names
from src.utils import format_amount, detect_user_lang

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------------

@dataclass
class Confirmation:
    token: str
    summary: str
    _is_selection: bool = False


@dataclass
class AgentResult:
    text: str
    confirmation: Confirmation | None = None


# ----------------------------------------------------------------------------
# Write-tool registry: these require user confirmation before executing
# ----------------------------------------------------------------------------

WRITE_TOOLS = {
    "save_sale",
    "record_payment",
    "save_production",
    "save_cash_flow",
    "create_customer",
}



def _serialize_tool_calls(message_tool_calls) -> list[dict[str, Any]]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in message_tool_calls
    ]


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

async def agent_loop(
    user_message: str,
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> AgentResult:
    """
    Run one user turn through the LLM.

    Returns an AgentResult. If the LLM stages a write tool, the result
    carries `confirmation` (token + summary) — bot.py renders inline buttons.
    Otherwise `text` is the final reply.

    Read tools execute inline; write tools are deferred. The whole batch is
    deferred if *any* tool in a single LLM response is a write — see
    WRITE_TOOLS for the list.
    """
    allowed, retry = check_rate_limit(user_id)
    if not allowed:
        logger.warning("Rate limited user %s (retry after %ss)", user_id, retry)
        return AgentResult(text=f"⏳ Apne bahut messages bhej diye. Kripiya ruk ke phir bhejein (~{retry}s).")

    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    final_text: str = ""
    confirmation: Confirmation | None = None

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.debug("Agent iter %s for user %s", iteration, user_id)
            chosen_model = GROQ_MODEL if iteration == 1 else GROQ_MODEL_FAST
            response = await call_llm(messages, model=chosen_model)

            if not response.choices or not response.choices[0].message:
                if chosen_model != GROQ_MODEL:
                    logger.warning(
                        "Empty response on fast model iter %s — retrying on %s",
                        iteration, GROQ_MODEL,
                    )
                    response = await call_llm(messages, model=GROQ_MODEL)
                if not response.choices or not response.choices[0].message:
                    logger.error("Empty response from Groq for user %s", user_id)
                    return AgentResult(text="❌ Sorry, main samajh nahi paya. Phir se try karein.")

            message = response.choices[0].message

            if not message.tool_calls:
                final_text = (message.content or "✅ Done.").strip()
                messages.append({"role": "assistant", "content": final_text})
                break

            assistant_msg = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": _serialize_tool_calls(message.tool_calls),
            }

            # Parse arguments + classify
            parsed_calls: list[tuple[Any, dict[str, Any], bool]] = []
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                    args["user_id"] = user_id  # trusted: always wins over any LLM-supplied value
                    parsed_calls.append((tc, args, False))
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.warning(
                        "Bad tool args for %s from user %s: %s",
                        tc.function.name, user_id, exc,
                    )
                    parsed_calls.append((tc, {}, True))

            has_write = any(
                tc.function.name in WRITE_TOOLS and not err
                for tc, _, err in parsed_calls
            )

            if has_write:
                # Defer ALL calls in this response — stage a confirmation.
                pending_calls = [
                    pending.PendingToolCall(
                        id=tc.id, name=tc.function.name, arguments=args,
                    )
                    for tc, args, err in parsed_calls if not err
                ]
                write_calls = [c for c in pending_calls if c.name in WRITE_TOOLS]
                customer_names = _extract_customer_names(messages)
                summary = _build_summary(write_calls, customer_names)
                user_lang = detect_user_lang(user_message)
                token = pending.put(pending.PendingAction(
                    user_id=user_id,
                    assistant_message=assistant_msg,
                    tool_calls=pending_calls,
                    summary=summary,
                    extras={
                        "user_lang": user_lang,
                        "customer_names": customer_names,
                        "original_message": user_message,
                        "username": username,
                        "first_name": first_name,
                    },
                ))
                confirmation = Confirmation(token=token, summary=summary)
                # Don't persist the unanswered tool_call to history — we'll add
                # it once the user confirms. This keeps history valid if cancelled.
                final_text = summary
                # Strip the user message we appended; it stays in history regardless
                break

            # No writes → execute every tool inline and continue the loop.
            messages.append(assistant_msg)
            selection_prompt = None  # Used if search_customer returns multiple close matches
            for tc, args, err in parsed_calls:
                if err:
                    tool_result = json.dumps({"ok": False, "error": "invalid tool arguments JSON"})
                else:
                    logger.info("tool_call user=%s name=%s", user_id, tc.function.name)
                    try:
                        tool_result = await execute_tool(tc.function.name, args)
                    except Exception as exc:
                        exc.add_note(f"tool={tc.function.name}, user_id={user_id}")
                        raise

                # Check if search_customer returned ambiguous matches needing UI selection
                if tc.function.name == "search_customer" and not err:
                    try:
                        result_data = json.loads(tool_result)
                        if result_data.get("selection_required"):
                            # Multiple close matches — show selection UI instead of LLM reasoning
                            user_lang = detect_user_lang(user_message)
                            customer_options = [
                                selection.CustomerOption(
                                    id=opt["id"],
                                    shop_name=opt["shop_name"],
                                )
                                for opt in result_data.get("customer_options", [])
                            ]
                            sel = selection.PendingSelection(
                                user_id=user_id,
                                original_message=user_message,
                                username=username,
                                first_name=first_name,
                                customer_options=customer_options,
                                extras={"user_lang": user_lang},
                            )
                            sel_token = selection.put(sel)
                            selection_prompt = (sel_token, customer_options, user_lang)
                            # Skip adding this tool result to messages; we'll return to bot instead
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": tool_result,
                })

            # If selection prompt was triggered, break out and return to bot
            if selection_prompt is not None:
                sel_token, customer_options, user_lang = selection_prompt
                if user_lang == "hi-Deva":
                    selection_text = "🔍 *कई ग्राहक मिले। नीचे से चुनिए:*"
                else:
                    selection_text = "🔍 *Multiple customers found. Please select one:*"
                final_text = selection_text
                confirmation = Confirmation(token=sel_token, summary=selection_text, _is_selection=True)
                break
        else:
            logger.warning("Max iterations reached for user %s", user_id)
            final_text = "⏱️ Bahut steps ho gaye. assan task dijiye."

    except (groq_sdk.GroqError, openai.APIError, ValueError, json.JSONDecodeError) as exc:
        logger.exception("Agent loop error for user %s", user_id)
        return AgentResult(text="❌ Kuch gadbad ho gayi. kripeya phir se try karein.")
    except Exception as exc:
        logger.exception("Unexpected agent error for user %s", user_id)
        exc.add_note(f"user_id={user_id}, loop failure")
        raise

    set_history(user_id, [m for m in messages if m["role"] != "system"])
    logger.info("Agent finished for user %s: %s", user_id, final_text[:80])
    return AgentResult(text=final_text or "✅ Done.", confirmation=confirmation)


# ----------------------------------------------------------------------------
# Resume after confirmation: execute the staged tool calls and ask LLM for a
# natural-language closing message.
# ----------------------------------------------------------------------------

async def continue_after_confirmation(
    user_id: int,
    action: pending.PendingAction,
) -> AgentResult:
    """Execute deferred tool calls and render closing via template (no LLM round-trip)."""
    history = get_history(user_id)
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    messages.append(action.assistant_message)

    extras = action.extras or {}
    user_lang = extras.get("user_lang", "hi-Latn")
    customer_names = dict(extras.get("customer_names") or {})
    customer_names.update(_extract_customer_names(messages))

    closing_lines: list[str] = []
    for call in action.tool_calls:
        logger.info("confirmed_tool_call user=%s name=%s", user_id, call.name)
        try:
            tool_result = await execute_tool(call.name, dict(call.arguments))
        except Exception:
            logger.exception("Confirmed tool %s failed for user %s", call.name, user_id)
            tool_result = json.dumps({"ok": False, "error": "internal error"})
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": tool_result,
        })
        if call.name in WRITE_TOOLS:
            closing_lines.append(_render_closing(call, tool_result, customer_names, user_lang))

    text = "\n\n".join(closing_lines) if closing_lines else "✅ Saved."

    # If a customer was just created and there's an original intent that was NOT
    # itself a customer creation, re-run it (e.g., "Sharma ko sale karo" → search failed
    # → create Sharma → re-run sale). Skip if user explicitly asked for create_customer.
    if any(c.name == "create_customer" for c in action.tool_calls):
        orig_msg = extras.get("original_message", "")
        # Only chain if original message doesn't look like a customer creation request
        is_explicit_create = (
            orig_msg and any(
                phrase in orig_msg.lower()
                for phrase in ["naya customer", "new customer", "banao", "bana do", "create customer"]
            )
        )
        if orig_msg and not is_explicit_create:
            creation_text = text
            messages.append({"role": "assistant", "content": text})
            set_history(user_id, [m for m in messages if m["role"] != "system"])

            follow_up = await agent_loop(
                user_message=orig_msg,
                user_id=user_id,
                username=extras.get("username", ""),
                first_name=extras.get("first_name", ""),
            )
            combined_text = creation_text + "\n\n" + follow_up.text
            return AgentResult(text=combined_text, confirmation=follow_up.confirmation)

    messages.append({"role": "assistant", "content": text})
    set_history(user_id, [m for m in messages if m["role"] != "system"])
    return AgentResult(text=text)


async def cancel_pending(user_id: int, action: pending.PendingAction) -> AgentResult:
    """User pressed ❌. Drop the staged action; mark cancellation in history so
    the LLM doesn't re-propose the same write on the next turn."""
    history = get_history(user_id)
    history.append({"role": "assistant", "content": "(User cancelled the pending action.)"})
    set_history(user_id, history)
    return AgentResult(text="❌ Cancelled. Kuch save nahi hua.")
