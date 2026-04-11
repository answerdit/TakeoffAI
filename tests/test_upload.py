"""Regression tests for bid upload fixes.

Pins two smoke-test findings from 2026-04-11:

1. The CSV content validator must accept UTF-8 (em dashes, accents) so
   the server's own template — which contains an em dash in its sample
   row — round-trips through ``POST /api/upload/bids/csv`` without
   hitting the binary-file guard.

2. ``update_client_profile_from_upload`` must deduplicate winning rows
   against existing ``winning_examples`` so re-uploading the same file
   does not double the historical_match agent's prompt window.
"""

import io
import json

import pytest
from httpx import ASGITransport, AsyncClient

import backend.agents.feedback_loop as fl
from backend.api.main import app
from backend.api.upload import _validate_csv_content


# ── Unit: UTF-8 validator accepts em dashes (Fix #1) ─────────────────────────


def test_csv_validator_accepts_em_dash():
    """Em dashes are valid UTF-8; must not be rejected as 'Invalid file content'."""
    content = "project_name,notes\nSmith Office \u2014 Remodel,won on value\n".encode("utf-8")
    _validate_csv_content(content)  # must not raise


def test_csv_validator_accepts_utf8_bom():
    """Excel writes CSVs with a UTF-8 BOM prefix; must not trip the guard."""
    content = b"\xef\xbb\xbfproject_name,zip_code\nTest,75001\n"
    _validate_csv_content(content)


def test_csv_validator_rejects_binary_null_bytes():
    """PNG header (contains NULs) must still be rejected."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _validate_csv_content(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    assert exc.value.status_code == 400


def test_csv_validator_rejects_invalid_utf8():
    """Lone continuation byte is not valid UTF-8; must be rejected."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _validate_csv_content(b"project_name\n\xff\xfe not utf-8\n")


def test_csv_validator_handles_multibyte_at_sample_boundary():
    """A valid UTF-8 file whose byte 512 lands mid-codepoint must still pass
    because _validate_csv_content only samples the first 512 bytes. Pads the
    front of the payload so the em dash straddles the 512-byte cutoff."""
    # 510 ASCII bytes + 3-byte em dash (\xe2\x80\x94) → dash spans bytes 510-512
    content = (b"x" * 510) + "\u2014 tail\n".encode("utf-8")
    _validate_csv_content(content)


# ── Integration: server template round-trips (Fix #1) ───────────────────────


@pytest.mark.anyio
async def test_csv_template_roundtrips_through_upload(tmp_path, monkeypatch):
    """The template served at /upload/template/csv contains an em dash in
    the sample row. Regression guard: it must upload cleanly back to the
    same server without tripping the content validator."""
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        tpl = await c.get(
            "/api/upload/template/csv", headers={"X-API-Key": "test-key"}
        )
        assert tpl.status_code == 200
        assert "\u2014" in tpl.text, "template lost its em dash — test premise broken"

        resp = await c.post(
            "/api/upload/bids/csv",
            files={"file": ("tpl.csv", io.BytesIO(tpl.content), "text/csv")},
            data={"client_id": "tpl-roundtrip"},
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows_imported"] == 1
    assert body["rows_won"] == 1


# ── Integration: dedup on re-upload (Fix #3) ─────────────────────────────────


CSV_WITH_TWO_WINS = (
    b"project_name,zip_code,bid_date,your_bid_amount,won\n"
    b"Job Alpha,75001,2024-03-15,100000,true\n"
    b"Job Beta,75002,2024-03-20,200000,true\n"
    b"Job Gamma,75003,2024-03-25,300000,false\n"
)


@pytest.mark.anyio
async def test_reupload_same_csv_is_deduped(tmp_path, monkeypatch):
    """Uploading the identical CSV twice must not double winning_examples.
    The historical_match agent reads the last 5 wins into its system prompt,
    so duplicates silently bias the agent toward whatever got re-uploaded."""
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    async def _upload():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.post(
                "/api/upload/bids/csv",
                files={"file": ("bids.csv", io.BytesIO(CSV_WITH_TWO_WINS), "text/csv")},
                data={"client_id": "dedup-test"},
                headers={"X-API-Key": "test-key"},
            )

    first = await _upload()
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["rows_imported"] == 3
    assert first_body["rows_won"] == 2
    assert first_body["rows_new_wins"] == 2
    assert first_body["rows_duplicate_wins"] == 0

    second = await _upload()
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["rows_imported"] == 3
    assert second_body["rows_won"] == 2
    # Key assertion: zero new wins added, both flagged as duplicates.
    assert second_body["rows_new_wins"] == 0
    assert second_body["rows_duplicate_wins"] == 2

    profile = json.loads((tmp_path / "dedup-test.json").read_text())
    assert len(profile["winning_examples"]) == 2, (
        "re-upload doubled the winning examples — dedup did not catch the duplicate rows"
    )

    # upload_stats semantics: total_uploaded counts attempts (6 rows across
    # 2 uploads); total_won_uploaded counts only NEW wins actually added.
    assert profile["upload_stats"]["total_uploaded"] == 6
    assert profile["upload_stats"]["total_won_uploaded"] == 2


def test_dedup_key_matches_legacy_project_summary_prefix(tmp_path, monkeypatch):
    """Profiles written before this fix stored project_name only in the
    ``project_summary`` string (em-dash-separated prefix). ``_upload_dedup_key``
    must back-fill from that prefix so a freshly uploaded bid matches the
    legacy row and dedups cleanly — no schema migration required."""
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    legacy_profile = {
        "client_id": "legacy-client",
        "winning_examples": [
            {
                "agent_name": "upload",
                "total_bid": 142800.0,
                "estimate_snapshot": {
                    # Old format: no explicit project_name field.
                    "project_summary": "Smith Office \u2014 interior renovation",
                    "trade_type": "general",
                },
                "timestamp": "2024-03-15",
                "source": "upload",
            }
        ],
        "agent_elo": {},
        "stats": {},
    }
    (tmp_path / "legacy-client.json").write_text(json.dumps(legacy_profile))

    new_bid = {
        "project_name": "Smith Office",
        "description": "interior renovation",
        "zip_code": "79601",
        "bid_date": "2024-03-15",
        "trade_type": "general",
        "your_bid_amount": 142800.0,
        "won": True,
    }

    stats: dict = {}
    fl.update_client_profile_from_upload("legacy-client", [new_bid], stats_out=stats)

    assert stats["new_winning_examples"] == 0
    assert stats["duplicate_winning_examples"] == 1

    profile = json.loads((tmp_path / "legacy-client.json").read_text())
    assert len(profile["winning_examples"]) == 1
