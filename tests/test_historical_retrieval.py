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


def test_all_resolved_statuses_accepted(patch_jobs_dir):
    """`won`, `lost`, and `closed` must all surface in retrieval. Regression
    guard: the resolved-status set is load-bearing — dropping any of the three
    silently starves downstream rerank of signal from that job class."""
    jobs_dir = patch_jobs_dir
    _write_job("won-job", _base_meta(status="won"), "body", jobs_dir)
    _write_job("lost-job", _base_meta(status="lost"), "body", jobs_dir)
    _write_job("closed-job", _base_meta(status="closed"), "body", jobs_dir)
    _write_job("prospect-job", _base_meta(status="prospect"), "body", jobs_dir)
    _write_job("tc-job", _base_meta(status="tournament-complete"), "body", jobs_dir)

    result = get_comparable_jobs("acme", "residential", "kitchen", zip_code="76801")
    outcomes = {r["outcome"] for r in result}
    assert outcomes == {"won", "lost", "closed"}


def test_score_formula_is_precise(patch_jobs_dir):
    """Pin the exact score math so silent retuning shows up as a test failure.
    Same client (+10) + trade matched (+5) + zip3 matched (+2) + jaccard(0.0)×3
    = 17.0 exactly when the body has no token overlap with the query."""
    jobs_dir = patch_jobs_dir
    _write_job(
        "precise",
        _base_meta(client="acme", trade="residential", zip="76801"),
        "zzz qqq xxx yyy",  # zero overlap with query below
        jobs_dir,
    )
    result = get_comparable_jobs("acme", "residential", "kitchen remodel", zip_code="76801")
    assert len(result) == 1
    assert result[0]["similarity_score"] == 17.0


def test_zip3_bonus_applied_only_on_prefix_match(patch_jobs_dir):
    """zip3 bonus is +2.0, applied only when the first three chars match.
    Two same-client jobs with different zips should see a 2.0 score gap."""
    jobs_dir = patch_jobs_dir
    _write_job(
        "near",
        _base_meta(client="acme", zip="76802"),  # shares 768 prefix
        "zzz qqq xxx",
        jobs_dir,
    )
    _write_job(
        "far",
        _base_meta(client="acme", zip="90210"),  # no prefix overlap
        "zzz qqq xxx",
        jobs_dir,
    )
    result = get_comparable_jobs("acme", "residential", "kitchen", zip_code="76801")
    by_name = {r["project_name"]: r for r in result}
    assert by_name["near"]["similarity_score"] - by_name["far"]["similarity_score"] == 2.0


def test_empty_query_zip_skips_zip_bonus_gracefully(patch_jobs_dir):
    """When caller passes zip_code='', retrieval must not crash and must
    simply skip the zip bonus. Score should equal 10 + 5 + 0 + 0 = 15.0."""
    jobs_dir = patch_jobs_dir
    _write_job("job", _base_meta(client="acme", zip="76801"), "zzz qqq", jobs_dir)
    result = get_comparable_jobs("acme", "residential", "kitchen", zip_code="")
    assert len(result) == 1
    assert result[0]["similarity_score"] == 15.0


def test_numeric_zip_in_frontmatter_still_matches(patch_jobs_dir):
    """YAML may parse '78701' as an int when no quotes are present. Retrieval
    coerces via str(), so numeric and string zips must both match. Regression
    guard: if anyone removes the str() coercion, this test catches it."""
    jobs_dir = patch_jobs_dir
    _write_job("numeric-zip", _base_meta(client="acme", zip=78701), "zzz", jobs_dir)
    result = get_comparable_jobs("acme", "residential", "kitchen", zip_code="78701")
    assert len(result) == 1
    # +2.0 zip bonus must have been applied
    assert result[0]["similarity_score"] == 17.0


def test_limit_parameter_caps_results(patch_jobs_dir):
    """Explicit limit must cap the return list while preserving rank order."""
    jobs_dir = patch_jobs_dir
    for i in range(7):
        _write_job(f"job-{i}", _base_meta(client="acme"), "zzz qqq xxx", jobs_dir)
    result = get_comparable_jobs("acme", "residential", "kitchen", limit=3)
    assert len(result) == 3


def test_gitkeep_file_is_ignored(patch_jobs_dir):
    """A `.gitkeep` file in jobs/ must not be parsed as a job page. Regression
    guard: the filename-based skip in retrieval is the only thing preventing
    _parse_frontmatter from raising on the empty placeholder file."""
    jobs_dir = patch_jobs_dir
    (jobs_dir / ".gitkeep").write_text("", encoding="utf-8")
    _write_job("real-job", _base_meta(client="acme"), "kitchen body", jobs_dir)

    result = get_comparable_jobs("acme", "residential", "kitchen", zip_code="76801")
    assert len(result) == 1
    assert result[0]["project_name"] == "real-job"
