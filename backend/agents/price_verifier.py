"""
Price Verifier Agent — TakeoffAI
Verifies LLM-generated unit prices against web sources.

Three trigger paths: background (post-judge), on-demand (API), nightly batch (CSV).
All results written to price_audit table; deviations >5% flagged to review_queue.
"""

import asyncio
import csv
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import httpx
from anthropic import Anthropic

anthropic_client = Anthropic()

DB_PATH = str(Path(__file__).parent.parent / "data" / "takeoffai.db")
CSV_PATH = Path(__file__).parent.parent / "data" / "material_costs.csv"

DEVIATION_THRESHOLD_PCT = 5.0   # flag if abs deviation > 5%
AGREEMENT_SPREAD_PCT = 10.0     # sources "agree" if within 10% of each other
MIN_SOURCES_FOR_AUTO_UPDATE = 3

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_SUPPLIER_URLS = {
    "homedepot": "https://www.homedepot.com/s/{query}",
    "lowes":     "https://www.lowes.com/search?searchTerm={query}",
}


async def _fetch_supplier_price(
    item: str,
    unit: str,
    supplier: str,
) -> Optional[float]:
    """
    Fetch a supplier search page and ask Claude to extract a unit price.

    Returns float price or None on any failure.
    """
    url_template = _SUPPLIER_URLS.get(supplier)
    if not url_template:
        return None

    url = url_template.format(query=quote_plus(item))

    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return None
            html = response.text[:12_000]  # first 12KB is enough for search results
    except Exception:
        return None

    # Ask Claude to extract the price from the HTML snippet
    try:
        msg = anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=64,
            system=(
                "You are a price extraction assistant. "
                "Given HTML from a supplier website, find the unit price for the specified item. "
                "Return ONLY the numeric price as a decimal (e.g. '2.45'). "
                "If no clear price is found, return exactly 'NO_PRICE'. No other text."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Item: {item}\nUnit: {unit}\n\n"
                    f"HTML:\n{html}"
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        if raw == "NO_PRICE":
            return None
        # Strip any currency symbols and parse
        numeric = re.sub(r"[^\d.]", "", raw)
        return float(numeric) if numeric else None
    except Exception:
        return None
