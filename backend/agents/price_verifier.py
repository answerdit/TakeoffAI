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
from anthropic import AsyncAnthropic

from backend.agents._db import _configure_conn
from backend.config import settings

anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key or None)

DB_PATH = settings.db_path
CSV_PATH = Path(__file__).parent.parent / "data" / "material_costs.csv"

DEVIATION_THRESHOLD_PCT = 5.0  # flag if abs deviation > 5%
AGREEMENT_SPREAD_PCT = 10.0  # sources "agree" if within 10% of each other
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
    "lowes": "https://www.lowes.com/search?searchTerm={query}",
}


def _extract_price_candidates(html: str) -> str:
    """
    Extract price-like patterns from raw HTML before sending to LLM.
    Returns a condensed string of candidate price mentions (max ~800 chars).
    Falls back to the first 800 chars of the raw HTML if no candidates found.
    """
    # Match dollar amounts: $1.23, $12.34, $123.45, $1,234.56
    # Also match "per unit" context lines
    candidates = re.findall(
        r'(?:^|[\s>"\'])(\$[\d,]+\.?\d*(?:\s*/\s*\w+)?)',
        html,
        re.MULTILINE,
    )
    if candidates:
        # Deduplicate, keep first 20
        seen = []
        for c in candidates:
            c = c.strip()
            if c not in seen:
                seen.append(c)
            if len(seen) >= 20:
                break
        return " | ".join(seen)
    # Fallback: strip tags, return first 800 chars of plain text
    plain = re.sub(r"<[^>]+>", " ", html)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:800]


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
        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=15.0, follow_redirects=True
        ) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return None
            html_raw = response.text[:12_000]  # first 12KB is enough for search results
            price_context = _extract_price_candidates(html_raw)
    except Exception:
        return None

    # Ask Claude to extract the price from the pre-filtered price candidates
    try:
        msg = await anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=64,
            system=(
                "You are a price extraction assistant. "
                "Given price information extracted from a supplier website, find the unit price for the specified item. "
                "Return ONLY the numeric price as a decimal (e.g. '2.45'). "
                "If no clear price is found, return exactly 'NO_PRICE'. No other text."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Item: {item}\nUnit: {unit}\n\n"
                        f"Price candidates from page:\n{price_context}"
                    ),
                }
            ],
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
        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=15.0, follow_redirects=True
        ) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return []
            html_raw = response.text[:20_000]
            price_context = _extract_price_candidates(html_raw)
    except Exception:
        return []

    try:
        msg = await anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=128,
            system=(
                "You are a price extraction assistant reading web search results. "
                "Extract all distinct unit prices you can find for the specified item. "
                "Return ONLY a comma-separated list of decimals (e.g. '2.45,2.60,2.55'). "
                "If no prices found, return 'NO_PRICE'. No other text."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Item: {item}\nUnit: {unit}\n\n"
                        f"Price candidates from page:\n{price_context}"
                    ),
                }
            ],
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

    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fieldnames = (
        list(rows[0].keys())
        if rows
        else ["item", "unit", "low_cost", "high_cost", "trade_category"]
    )

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
        mode="w", suffix=".csv", dir=csv_path.parent, delete=False, newline="", encoding="utf-8"
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = Path(tmp.name)

    tmp_path.replace(csv_path)
    return True


async def _write_audit_record(
    db_path: str,
    triggered_by: str,
    tournament_id: Optional[int],
    line_item: str,
    unit: str,
    ai_unit_cost: float,
    all_prices: list[float],
    sources_meta: list[dict],
) -> dict:
    """Write one row to price_audit and optionally to review_queue. Returns audit dict."""
    import aiosqlite

    source_count = len(all_prices)
    verified_low = min(all_prices) if all_prices else None
    verified_high = max(all_prices) if all_prices else None
    verified_mid = round(sum(all_prices) / len(all_prices), 4) if all_prices else None
    deviation_pct = _compute_deviation(ai_unit_cost, verified_mid) if verified_mid else None
    flagged = (
        1 if (deviation_pct is not None and abs(deviation_pct) > DEVIATION_THRESHOLD_PCT) else 0
    )
    sources_json = json.dumps(sources_meta)

    async with aiosqlite.connect(db_path) as db:
        await _configure_conn(db)
        async with db.execute(
            """
            INSERT INTO price_audit
              (triggered_by, tournament_id, line_item, unit, ai_unit_cost,
               verified_low, verified_high, verified_mid, deviation_pct,
               sources, source_count, flagged)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                triggered_by,
                tournament_id,
                line_item,
                unit,
                ai_unit_cost,
                verified_low,
                verified_high,
                verified_mid,
                deviation_pct,
                sources_json,
                source_count,
                flagged,
            ),
        ) as cur:
            audit_id = cur.lastrowid
        await db.commit()

        if flagged:
            await db.execute(
                """
                INSERT INTO review_queue
                  (audit_id, line_item, unit, ai_unit_cost, verified_mid,
                   deviation_pct, sources)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    audit_id,
                    line_item,
                    unit,
                    ai_unit_cost,
                    verified_mid,
                    deviation_pct,
                    sources_json,
                ),
            )
            await db.commit()

    return {
        "audit_id": audit_id,
        "line_item": line_item,
        "unit": unit,
        "ai_unit_cost": ai_unit_cost,
        "verified_low": verified_low,
        "verified_high": verified_high,
        "verified_mid": verified_mid,
        "deviation_pct": deviation_pct,
        "source_count": source_count,
        "flagged": flagged,
        "auto_updated": 0,
    }


async def verify_line_items(
    line_items: list[dict],
    triggered_by: str,
    tournament_id: Optional[int] = None,
) -> list[dict]:
    """
    Verify unit prices for a list of estimate line items against web sources.

    For each item:
    1. Try Home Depot + Lowe's supplier lookup (Phase 1)
    2. If < 2 results, add DuckDuckGo web search prices (Phase 2)
    3. If 3+ agreeing sources (within 10%): auto-update material_costs.csv
    4. Write audit record; flag + queue if deviation > 5%

    Returns list of audit record dicts.
    """
    records = []

    for item in line_items:
        description = item.get("description", "")
        unit = item.get("unit", "EA")
        ai_unit_cost = float(item.get("unit_material_cost", 0.0))

        if not description or ai_unit_cost == 0:
            continue

        all_prices: list[float] = []
        sources_meta: list[dict] = []
        retrieved_at = datetime.now(timezone.utc).isoformat()

        # Phase 1: Supplier sites
        for supplier in ("homedepot", "lowes"):
            price = await _fetch_supplier_price(description, unit, supplier)
            if price is not None:
                all_prices.append(price)
                sources_meta.append(
                    {
                        "source": supplier,
                        "price": price,
                        "retrieved_at": retrieved_at,
                    }
                )

        # Phase 2: Web search fallback if < 2 results from supplier lookup
        if len(all_prices) < 2:
            web_prices = await _web_search_price(description, unit)
            for p in web_prices[:3]:  # cap at 3 web results
                all_prices.append(p)
                sources_meta.append(
                    {
                        "source": "web_search",
                        "price": p,
                        "retrieved_at": retrieved_at,
                    }
                )

        # Phase 3: Confidence decision
        auto_updated = 0
        if len(all_prices) >= MIN_SOURCES_FOR_AUTO_UPDATE and _sources_agree(all_prices):
            updated = _update_seed_csv(
                item=description,
                new_low=min(all_prices),
                new_high=max(all_prices),
                csv_path=CSV_PATH,
            )
            if updated:
                auto_updated = 1

        record = await _write_audit_record(
            db_path=DB_PATH,
            triggered_by=triggered_by,
            tournament_id=tournament_id,
            line_item=description,
            unit=unit,
            ai_unit_cost=ai_unit_cost,
            all_prices=all_prices,
            sources_meta=sources_meta,
        )
        record["auto_updated"] = auto_updated

        # Persist auto_updated flag to DB
        if auto_updated:
            import aiosqlite

            async with aiosqlite.connect(DB_PATH) as db:
                await _configure_conn(db)
                await db.execute(
                    "UPDATE price_audit SET auto_updated = 1 WHERE id = ?",
                    (record["audit_id"],),
                )
                await db.commit()

        records.append(record)

    return records
