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
from dataclasses import dataclass
from typing import Any

import groq as groq_sdk
import openai

from src.config import (
    GROQ_MODEL, GROQ_MODEL_FAST,
    get_system_prompt, TOOLS, MAX_ITERATIONS,
)
from src.providers import call_llm
from src.session import (
    get_history, set_history, clear_history, check_rate_limit,
    _compact_tool_result, inject_created_customer,
)
from src.tools import execute_tool
from src import pending, selection
from src.render import _build_summary, _render_closing, _extract_customer_names
from src.utils import detect_user_lang
from src import logger as log_utils

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
    "delete_record",
}

# Irrelevant fields to remove from tool results when storing in history
_TOOL_RESULT_KEEP_FIELDS = {
    "search_customer": {"ok", "results", "count", "selection_required", "customer_options", "not_found"},
    "create_customer": {"ok", "customer_id", "shop_name"},
    "save_sale": {"ok", "sale_id", "total_bill"},
    "record_payment": {"ok", "ledger_id", "new_balance"},
    "get_customer_balance": {"ok", "customer_id", "outstanding_balance", "credit_limit", "not_found"},
    "get_all_balances": {"ok", "balances"},
    "save_production": {"ok", "id"},
    "save_cash_flow": {"ok", "id"},
    "get_customer": {"ok", "customer", "not_found"},
    "query_customers": {"ok", "customers", "count"},
    "query_production": {"ok", "production", "count", "total_kg"},
    "query_cash_flow": {"ok", "cash_flows", "count", "total_in", "total_out"},
    "query_credit_ledger": {"ok", "ledger", "count", "total_debit", "total_credit"},
    "query_sales": {"ok", "sales"},
    "delete_record": {"ok", "success"},
    "get_cash_position": {"ok", "total_in", "total_out", "net_cash"},
}

def _clean_tool_result(tool_name: str, content: str) -> str:
    """Remove irrelevant fields from tool result JSON. Keep only essential data."""
    if not content:
        return content
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content
    if not isinstance(payload, dict):
        return content
    keep_fields = _TOOL_RESULT_KEEP_FIELDS.get(tool_name, {"ok"})
    cleaned = {k: v for k, v in payload.items() if k in keep_fields}
    return json.dumps(cleaned)



def _serialize_tool_calls(message_tool_calls) -> list[dict[str, Any]]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in message_tool_calls
    ]


def _dedupe_tool_calls(tool_calls: list[Any]) -> list[Any]:
    """Drop later tool_calls whose (name + normalized arguments) match an earlier one.

    Some smaller models (e.g. llama-3.1-8b-instant) hallucinate identical tool_calls
    in a single response — most commonly create_customer twice for the same shop.
    Compare on parsed-and-sorted arguments so trivial whitespace/key-order
    differences in the JSON string don't defeat the check.
    """
    seen: set[str] = set()
    deduped: list[Any] = []
    for tc in tool_calls:
        try:
            args_obj = json.loads(tc.function.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            args_obj = tc.function.arguments  # keep raw so malformed calls still surface
        key = f"{tc.function.name}::{json.dumps(args_obj, sort_keys=True, default=str)}"
        if key in seen:
            log_utils.log_error(0, "DuplicateToolCall", f"Dropped duplicate: {tc.function.name}")
            continue
        seen.add(key)
        deduped.append(tc)
    return deduped


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

async def agent_loop(
    user_message: str,
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    extras: dict[str, Any] | None = None,
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

    log_utils.log_user_message(user_id, user_message)

    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})

    messages: list[dict[str, Any]] = [{"role": "system", "content": get_system_prompt()}, *history]

    final_text: str = ""
    confirmation: Confirmation | None = None
    tools_called: list[str] = []
    iteration_count: int = 0

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            iteration_count = iteration
            chosen_model = GROQ_MODEL

            # Log the exact messages being sent
            log_utils.log_llm_request(user_id, iteration, chosen_model, messages)

            response = await call_llm(messages, model=chosen_model)

            # Log the raw response
            log_utils.log_llm_response(user_id, iteration, response)

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

            # Log raw tool calls before dedup
            raw_count = len(message.tool_calls)
            tool_calls = _dedupe_tool_calls(message.tool_calls)
            if len(tool_calls) < raw_count:
                log_utils.log_error(user_id, "DedupResult", f"Dropped {raw_count - len(tool_calls)} duplicates, kept {len(tool_calls)}")

            assistant_msg = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": _serialize_tool_calls(tool_calls),
            }

            # Parse arguments + classify
            parsed_calls: list[tuple[Any, dict[str, Any], bool]] = []
            for tc in tool_calls:
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
                user_lang = detect_user_lang(user_message)
                summary = _build_summary(write_calls, customer_names, user_lang)
                token = pending.put(pending.PendingAction(
                    user_id=user_id,
                    assistant_message=assistant_msg,
                    tool_calls=pending_calls,
                    summary=summary,
                    extras={
                        **(extras or {}),
                        "user_lang": user_lang,
                        "customer_names": customer_names,
                        "original_message": user_message,
                        "username": username,
                        "first_name": first_name,
                    },
                ))
                tool_call_dicts = [{"name": c.name, "args": c.arguments} for c in write_calls]
                log_utils.log_confirmation_staged(user_id, summary, tool_call_dicts)
                confirmation = Confirmation(token=token, summary=summary)

                # Add assistant message to history so next turn sees the full conversation
                # This keeps the conversation coherent even if user cancels
                messages.append(assistant_msg)
                set_history(user_id, [m for m in messages if m["role"] != "system"])

                final_text = summary
                break

            # No writes → execute every tool inline and continue the loop.
            messages.append(assistant_msg)
            selection_prompt = None  # Used if search_customer returns multiple close matches
            for tc, args, err in parsed_calls:
                if err:
                    tool_result = json.dumps({"ok": False, "error": "invalid tool arguments JSON"})
                else:
                    tools_called.append(tc.function.name)
                    try:
                        tool_result = await execute_tool(tc.function.name, args)
                        log_utils.log_tool_execution(user_id, tc.function.name, args, tool_result)
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

                compacted_result = _compact_tool_result(tool_result)
                cleaned_result = _clean_tool_result(tc.function.name, compacted_result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": cleaned_result,
                })

            # If selection prompt was triggered, break out and return to bot
            if selection_prompt is not None:
                sel_token, customer_options, user_lang = selection_prompt
                if user_lang == "hi-Deva":
                    selection_text = "🔍 *कई ग्राहक मिले। नीचे से चुनिए:*"
                elif user_lang == "en":
                    selection_text = "🔍 *Multiple customers found. Please select:*"
                else:  # hi-Hind
                    selection_text = "🔍 *Multiple customers found. Please select one:*"
                options_dicts = [{"id": opt.id, "shop_name": opt.shop_name} for opt in customer_options]
                log_utils.log_selection_ui(user_id, options_dicts)
                final_text = selection_text
                confirmation = Confirmation(token=sel_token, summary=selection_text, _is_selection=True)
                break
        else:
            log_utils.log_agent_error(user_id, "max_iterations")
            final_text = "⏱️ Bahut steps ho gaye. assan task dijiye."

    except groq_sdk.RateLimitError as exc:
        log_utils.log_error(user_id, "RateLimitError", str(exc))
        return AgentResult(text="⏳ Server busy. Please retry in a few moments.")
    except (groq_sdk.GroqError, openai.APIError, ValueError, json.JSONDecodeError) as exc:
        log_utils.log_error(user_id, type(exc).__name__, str(exc))
        return AgentResult(text="❌ Kuch gadbad ho gayi. kripeya phir se try karein.")
    except Exception as exc:
        log_utils.log_error(user_id, "UnexpectedError", str(exc))
        exc.add_note(f"user_id={user_id}, loop failure")
        raise

    set_history(user_id, [m for m in messages if m["role"] != "system"])

    log_utils.log_query_result(user_id, final_text, iteration_count, tools_called)
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
    messages: list[dict[str, Any]] = [{"role": "system", "content": get_system_prompt()}, *history]
    messages.append(action.assistant_message)

    extras = action.extras or {}
    user_lang = extras.get("user_lang", "hi-Hind")
    customer_names = dict(extras.get("customer_names") or {})
    customer_names.update(_extract_customer_names(messages))

    tool_calls_to_execute = [{"name": c.name, "args": c.arguments} for c in action.tool_calls]
    log_utils.log_confirmation_executed(user_id, tool_calls_to_execute)

    closing_lines: list[str] = []
    for call in action.tool_calls:
        try:
            tool_result = await execute_tool(call.name, dict(call.arguments))
            log_utils.log_tool_execution(user_id, call.name, dict(call.arguments), tool_result)
        except Exception as e:
            log_utils.log_error(user_id, f"ToolExecutionFailed", f"{call.name}: {str(e)}")
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
        # Guard against cascading re-entries: if this is already a chained re-run, don't chain again
        if extras.get("_chained"):
            messages.append({"role": "assistant", "content": text})
            set_history(user_id, [m for m in messages if m["role"] != "system"])
            return AgentResult(text=text)

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

            # Extract customer_id from the tool result to inject it before re-run
            customer_id = None
            shop_name = None
            for call, tool_result in zip(action.tool_calls, action.tool_results or []):
                if call.name == "create_customer":
                    try:
                        result_data = json.loads(tool_result) if isinstance(tool_result, str) else tool_result
                        customer_id = result_data.get("customer_id")
                        shop_name = call.arguments.get("shop_name") if call.arguments else None
                        break
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass

            # Inject the newly-created customer into history before re-running the original message
            if customer_id and shop_name:
                inject_created_customer(user_id, customer_id, shop_name)

            follow_up = await agent_loop(
                user_message=orig_msg,
                user_id=user_id,
                username=extras.get("username", ""),
                first_name=extras.get("first_name", ""),
                extras={**extras, "_chained": True},
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
