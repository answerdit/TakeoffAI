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


async def _web_search_price(item: str, unit: str) -> list[float]:
    """
    Search DuckDuckGo for current unit prices. Returns list of floats found.
    Falls back to empty list on any failure.
    """
    query = quote_plus(f"{item} price per {unit} 2026 construction material cost")
    url = f"https://html.duckduckgo.com/html/?q={query}"

    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return []
            html = response.text[:20_000]
    except Exception:
        return []

    try:
        msg = anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=128,
            system=(
                "You are a price extraction assistant reading web search results. "
                "Extract all distinct unit prices you can find for the specified item. "
                "Return ONLY a comma-separated list of decimals (e.g. '2.45,2.60,2.55'). "
                "If no prices found, return 'NO_PRICE'. No other text."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Item: {item}\nUnit: {unit}\n\n"
                    f"Search results HTML:\n{html}"
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        if raw == "NO_PRICE":
            return []
        prices = []
        for part in raw.split(","):
            numeric = re.sub(r"[^\d.]", "", part.strip())
            if numeric:
                try:
                    prices.append(float(numeric))
                except ValueError:
                    pass
        return prices
    except Exception:
        return []


def _compute_deviation(ai: float, verified_mid: float) -> float:
    """Percent deviation of AI price from verified midpoint. Positive = AI over-estimated."""
    if verified_mid == 0:
        return 0.0
    return round((ai - verified_mid) / verified_mid * 100, 4)


def _sources_agree(prices: list[float]) -> bool:
    """True if all prices are within AGREEMENT_SPREAD_PCT of each other."""
    if len(prices) < 2:
        return True
    lo, hi = min(prices), max(prices)
    if lo == 0:
        return False
    spread_pct = (hi - lo) / lo * 100
    return spread_pct <= AGREEMENT_SPREAD_PCT


def _update_seed_csv(
    item: str,
    new_low: float,
    new_high: float,
    csv_path: Path = CSV_PATH,
) -> bool:
    """
    Update low_cost and high_cost for a matching item row in material_costs.csv.
    Uses a temp-file-then-rename pattern for atomicity.
    Returns True if row was found and updated, False otherwise.
    """
    if not csv_path.exists():
        return False

    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    fieldnames = list(rows[0].keys()) if rows else ["item", "unit", "low_cost", "high_cost", "trade_category"]

    updated = False
    for row in rows:
        if row["item"].strip().lower() == item.strip().lower():
            row["low_cost"] = str(round(new_low, 4))
            row["high_cost"] = str(round(new_high, 4))
            updated = True

    if not updated:
        return False

    # Write atomically via temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", dir=csv_path.parent,
        delete=False, newline="", encoding="utf-8"
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = Path(tmp.name)

    tmp_path.replace(csv_path)
    return True
