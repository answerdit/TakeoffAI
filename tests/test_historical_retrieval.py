"""Tests for historical bid retrieval — empty vault, filtering, ranking, formatting."""

import pytest

import backend.agents._wiki_io as _io
from backend.agents.historical_retrieval import format_comparables_for_prompt, get_comparable_jobs


@pytest.fixture(autouse=True)
def patch_jobs_dir(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(_io, "JOBS_DIR", jobs_dir)
    return jobs_dir


def _write_job(name: str, meta: dict, body: str, jobs_dir=None):
    if jobs_dir is None:
        jobs_dir = _io.JOBS_DIR
    path = jobs_dir / f"{name}.md"
    _io._write_page(path, meta, body)
    return path


def _base_meta(**overrides) -> dict:
    base = {
        "status": "won",
        "client": "acme",
        "date": "2025-06-01",
        "trade": "residential",
        "zip": "76801",
        "our_bid": 50000,
        "estimate_total": 48000,
        "actual_cost": 45000,
        "outcome_date": "2025-07-01",
    }
    base.update(overrides)
    return base


def test_empty_vault_returns_empty_list(patch_jobs_dir):
    result = get_comparable_jobs("acme", "residential", "kitchen remodel")
    assert result == []


def test_filters_unresolved_jobs(patch_jobs_dir):
    jobs_dir = patch_jobs_dir
    _write_job("prospect-job", _base_meta(status="prospect"), "prospect body", jobs_dir)
    _write_job("submitted-job", _base_meta(status="bid-submitted"), "submitted body", jobs_dir)
    _write_job("won-job", _base_meta(status="won"), "won body", jobs_dir)

    result = get_comparable_jobs("acme", "residential", "kitchen remodel")
    assert len(result) == 1
    assert result[0]["outcome"] == "won"


def test_requires_trade_match(patch_jobs_dir):
    jobs_dir = patch_jobs_dir
    _write_job("res-job", _base_meta(trade="residential"), "residential kitchen", jobs_dir)
    _write_job("com-job", _base_meta(trade="commercial"), "commercial office", jobs_dir)

    result = get_comparable_jobs("acme", "residential", "kitchen remodel")
    assert len(result) == 1
    assert result[0]["trade"] == "residential"


def test_same_client_outranks_different_client(patch_jobs_dir):
    jobs_dir = patch_jobs_dir
    _write_job("same-client", _base_meta(client="acme"), "kitchen remodel work", jobs_dir)
    _write_job("diff-client", _base_meta(client="other"), "kitchen remodel work", jobs_dir)

    result = get_comparable_jobs("acme", "residential", "kitchen remodel")
    assert len(result) == 2
    assert result[0]["project_name"] == "same-client"


def test_description_similarity_ordering(patch_jobs_dir):
    jobs_dir = patch_jobs_dir
    high_overlap_body = (
        "kitchen remodel cabinets countertops tile flooring fixtures removal installation"
    )
    low_overlap_body = "roof replacement shingles gutters flashing drainage repair"
    _write_job("high-overlap", _base_meta(client="acme"), high_overlap_body, jobs_dir)
    _write_job("low-overlap", _base_meta(client="acme"), low_overlap_body, jobs_dir)

    result = get_comparable_jobs("acme", "residential", "kitchen remodel cabinets countertops tile")
    assert len(result) == 2
    assert result[0]["project_name"] == "high-overlap"


def test_format_empty_list_returns_empty_string():
    assert format_comparables_for_prompt([]) == ""


def test_format_includes_key_numbers():
    jobs = [
        {
            "project_name": "kitchen-reno",
            "date": "2025-11-03",
            "trade": "residential",
            "zip": "76801",
            "our_bid": 47500,
            "estimate_total": None,
            "actual_cost": 44120,
            "outcome": "won",
            "similarity_score": 12.0,
        }
    ]
    output = format_comparables_for_prompt(jobs)
    assert "47,500" in output
    assert "44,120" in output


def test_skips_malformed_files(patch_jobs_dir, caplog):
    jobs_dir = patch_jobs_dir
    bad_path = jobs_dir / "bad-yaml.md"
    bad_path.write_text("---\n: invalid: yaml: [\n---\nbody\n", encoding="utf-8")
    _write_job("good-job", _base_meta(), "good body", jobs_dir)

    import logging

    with caplog.at_level(logging.WARNING):
        result = get_comparable_jobs("acme", "residential", "kitchen remodel")

    assert len(result) == 1
    assert result[0]["project_name"] == "good-job"
