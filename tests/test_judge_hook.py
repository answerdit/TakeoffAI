import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_judge_triggers_background_verification():
    """After judging, verify_line_items is called as a background task."""
    called_args = {}

    async def fake_verify(line_items, triggered_by, tournament_id=None):
        called_args["triggered_by"] = triggered_by
        called_args["tournament_id"] = tournament_id
        return []

    import json
    import tempfile
    import aiosqlite
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        from backend.api.main import _CREATE_TABLES
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO bid_tournaments (id, client_id, project_description, zip_code, status) "
                "VALUES (?,?,?,?,?)",
                (1, "client1", "test project", "76801", "pending")
            )
            line_items_json = json.dumps({
                "line_items": [
                    {"description": "Concrete", "unit": "CY", "unit_material_cost": 150.0,
                     "quantity": 10, "subtotal": 1500.0}
                ]
            })
            await db.execute(
                "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, line_items_json, won) "
                "VALUES (?,?,?,?,?)",
                (1, "balanced", 10000.0, line_items_json, 0)
            )
            await db.commit()

        with patch("backend.agents.judge.DB_PATH", db_path):
            with patch("backend.agents.judge.asyncio.to_thread", new=AsyncMock(return_value={})):
                with patch("backend.agents.judge.verify_line_items", new=fake_verify):
                    from backend.agents.judge import judge_tournament
                    await judge_tournament(
                        tournament_id=1,
                        winner_agent_name="balanced",
                    )
                    # Give background task a moment to run
                    import asyncio
                    await asyncio.sleep(0.05)

    assert called_args.get("triggered_by") == "background"
    assert called_args.get("tournament_id") == 1
