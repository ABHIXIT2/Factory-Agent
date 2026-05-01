"""
Agent loop: Groq LLM with tool-calling.
Implements: Parse → Call tools → Gather results → Respond.
"""

import logging
import json
from typing import List, Dict, Any, Tuple, Optional
from groq import Groq
from src.config import (
    GROQ_API_KEY, SYSTEM_PROMPT, TOOLS,
    MAX_ITERATIONS, CONTEXT_WINDOW
)
from src.tools import execute_tool

logger = logging.getLogger(__name__)

# Initialize Groq client
client = Groq(api_key=GROQ_API_KEY)

# Session store: user_id → list of messages
sessions: Dict[int, List[Dict[str, str]]] = {}


def get_conversation_history(user_id: int) -> List[Dict[str, str]]:
    """Get last N messages for this user."""
    if user_id not in sessions:
        sessions[user_id] = []
    return sessions[user_id][-CONTEXT_WINDOW:]


def add_to_history(user_id: int, role: str, content: str) -> None:
    """Add a message to conversation history."""
    if user_id not in sessions:
        sessions[user_id] = []
    sessions[user_id].append({"role": role, "content": content})


def clear_history(user_id: int) -> None:
    """Clear conversation history after transaction."""
    if user_id in sessions:
        del sessions[user_id]


async def agent_loop(
    user_message: str,
    user_id: int,
    username: str = None,
    first_name: str = None
) -> str:
    """
    Main agent loop: Parse → Tool calls → Gather results → Respond.

    Args:
        user_message: The user's input
        user_id: Telegram user ID
        username: Telegram username (optional)
        first_name: Telegram first name (optional)

    Returns:
        Response to send to user
    """
    # Add user message to history
    add_to_history(user_id, "user", user_message)

    # Build messages for Groq API
    conversation_history = get_conversation_history(user_id)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *conversation_history
    ]

    iteration = 0
    while iteration < MAX_ITERATIONS:
        iteration += 1
        logger.debug(f"Agent iteration {iteration} for user {user_id}")

        try:
            # Call Groq LLM with tools
            response = client.chat.completions.create(
                model="llama-3.1-70b-versatile",  # Available on Groq free tier
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=1024,
                temperature=0.3
            )

            # Check if we got a response
            if not response.choices or not response.choices[0].message:
                logger.error("Empty response from Groq")
                return "❌ Sorry, I didn't understand. Please try again."

            message = response.choices[0].message

            # Case 1: LLM returned text (no tool calls) — conversation is done
            if not message.tool_calls:
                final_response = message.content or "✅ Done."
                add_to_history(user_id, "assistant", final_response)
                logger.info(f"Agent finished: {final_response[:50]}...")
                return final_response

            # Case 2: LLM wants to call tools
            tool_results = []

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_input = json.loads(tool_call.function.arguments)

                # Inject user_id if not already present
                if "user_id" not in tool_input:
                    tool_input["user_id"] = user_id

                logger.debug(f"Tool call: {tool_name} with {tool_input}")

                # Execute tool
                tool_result = execute_tool(tool_name, tool_input)

                tool_results.append({
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_name,
                    "result": tool_result
                })

            # Add assistant's tool-call message to history
            add_to_history(user_id, "assistant", f"[Calling: {', '.join(tr['tool_name'] for tr in tool_results)}]")

            # Add tool results back to messages
            messages.append({"role": "assistant", "content": message.content, "tool_calls": message.tool_calls})
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tr["tool_call_id"],
                        "content": tr["result"]
                    }
                    for tr in tool_results
                ]
            })

        except Exception as e:
            logger.error(f"Agent loop error: {e}", exc_info=True)
            return f"❌ Error: {str(e)}"

    # Max iterations reached
    logger.warning(f"Max iterations ({MAX_ITERATIONS}) reached for user {user_id}")
    return "⏱️ Transaction took too long. Please try again with a simpler request."
