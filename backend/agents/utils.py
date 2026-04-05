"""
Shared utilities for TakeoffAI agents.
"""

import asyncio
import json

from anthropic import AsyncAnthropic, RateLimitError, APIStatusError


def parse_llm_json(raw: str) -> dict:
    """
    Strip markdown fences and parse JSON from an LLM response.
    Raises json.JSONDecodeError if the response cannot be parsed.
    """
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        # parts[1] is the fenced block; strip an optional language tag
        text = parts[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


async def call_with_json_retry(
    client: AsyncAnthropic,
    *,
    model: str,
    max_tokens: int,
    system: str,
    messages: list,
    max_retries: int = 2,
    temperature: float = 0.7,
) -> dict:
    """
    Call the Anthropic messages API (async) and parse the response as JSON.

    Retries up to max_retries times on:
    - JSON parse failure (appends a correction nudge to the conversation)
    - 429 RateLimitError (exponential backoff)
    - 529 APIStatusError/overloaded (exponential backoff)

    Args:
        client:      An anthropic.AsyncAnthropic client instance.
        model:       Model ID string.
        max_tokens:  Token budget for the response.
        system:      System prompt string (unused directly; callers embed it in messages).
        messages:    List of message dicts (role/content).
        max_retries: How many additional attempts after the first failure.
        temperature: Sampling temperature (0.0–2.0), default 0.7.

    Returns:
        Parsed dict from the LLM response.

    Raises:
        ValueError: If all attempts fail to produce valid JSON.
        RateLimitError / APIStatusError: Re-raised after exhausting retries.
    """
    conversation = list(messages)
    last_error: Exception | None = None

    for attempt in range(1 + max_retries):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=conversation,
                temperature=temperature,
            )
            raw = response.content[0].text

            try:
                return parse_llm_json(raw)
            except (json.JSONDecodeError, IndexError, ValueError) as exc:
                last_error = exc
                if attempt < max_retries:
                    # Append the bad response and a correction nudge, then retry
                    conversation = conversation + [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                "Your response was not valid JSON. "
                                "Return ONLY the JSON object — no markdown fences, no explanation, no extra text."
                            ),
                        },
                    ]

        except RateLimitError:
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise

        except APIStatusError as e:
            if e.status_code == 529 and attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise

    raise ValueError(f"Failed to parse JSON after {1 + max_retries} attempts: {last_error}") from last_error
