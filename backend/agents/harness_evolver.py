"""
Harness Evolver — TakeoffAI
Observes agent win-rate imbalance and evolves underperforming PERSONALITY_PROMPTS
in tournament.py using Claude as a proposer. Each successful evolution is committed
to git — rollback and fork are native git operations.
"""

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

MIN_TOURNAMENTS = 10
DOMINANCE_THRESHOLD = 0.60
HARNESS_EVOLVER_MODEL = os.getenv("HARNESS_EVOLVER_MODEL", "claude-sonnet-4-6")
HARNESS_EVOLVER_MAX_TOOL_CALLS = int(os.getenv("HARNESS_EVOLVER_MAX_TOOL_CALLS", "30"))

TOURNAMENT_PY = Path(__file__).parent / "tournament.py"

_evolution_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _evolution_lock
    if _evolution_lock is None:
        _evolution_lock = asyncio.Lock()
    return _evolution_lock


def _get_generation_number() -> int:
    """Count prior harness evolution git commits."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--grep=harness: gen"],
            capture_output=True,
            text=True,
            cwd=TOURNAMENT_PY.parent.parent.parent,
        )
        lines = [line for line in result.stdout.strip().splitlines() if line]
        return len(lines)
    except Exception:
        return 0


def _replace_prompt_in_source(source: str, agent_name: str, new_prompt: str) -> str:
    """Surgically replace the triple-quoted string for agent_name in tournament.py source."""
    pattern = rf'("{agent_name}":\s*""")(.*?)(""")'
    return re.sub(
        pattern,
        lambda m: m.group(1) + new_prompt + m.group(3),
        source,
        flags=re.DOTALL,
    )


def check_dominance(client_id: str) -> bool:
    """
    Return True if one agent has won > DOMINANCE_THRESHOLD of this client's
    tournaments and MIN_TOURNAMENTS have been played.
    Called synchronously from judge.py before firing a background evolution task.
    """
    from backend.agents.feedback_loop import _profile_path

    path = _profile_path(client_id)
    if not path.exists():
        return False

    profile = json.loads(path.read_text())
    total = profile.get("stats", {}).get("total_tournaments", 0)
    if total < MIN_TOURNAMENTS:
        return False

    win_rates = profile.get("stats", {}).get("win_rate_by_agent", {})
    if not win_rates:
        return False

    return max(win_rates.values()) > DOMINANCE_THRESHOLD


def _git_commit(repo_root: Path, tournament_py: Path, commit_msg: str) -> str | None:
    try:
        subprocess.run(["git", "add", str(tournament_py)], cwd=repo_root, check=True)
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_root, check=True)
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


# ── Agentic proposer tools ────────────────────────────────────────────────────

_TOOLS = [
    {
        "name": "list_traces",
        "description": (
            "List available trace files for a client. Returns file paths with metadata "
            "(agent_name, tournament_id, total_bid, timestamp). Use to find which "
            "tournaments to investigate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "agent_name": {
                    "type": "string",
                    "description": "Filter by agent name (optional)",
                },
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file from the data directory. Use to read trace files or the client "
            "profile. Path must be under backend/data/."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative or absolute path. Must be under backend/data/.",
                }
            },
            "required": ["path"],
        },
    },
]


def _handle_list_traces(
    data_dir: Path,
    client_id: str,
    agent_name: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return metadata list for trace files matching client_id (and optionally agent_name)."""
    pattern = f"*/{agent_name or '*'}.json"
    files = sorted(
        data_dir.glob(pattern),
        key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0,
        reverse=True,
    )
    results = []
    for f in files:
        try:
            meta = json.loads(f.read_text())
            if meta.get("client_id") != client_id:
                continue
            results.append({
                "path": str(f.relative_to(data_dir)),
                "agent_name": meta.get("agent_name"),
                "tournament_id": meta.get("tournament_id"),
                "total_bid": meta.get("estimate", {}).get("total_bid"),
                "timestamp": meta.get("timestamp"),
            })
            if len(results) >= limit:
                break
        except Exception:
            continue
    return results


def _handle_read_file(data_dir: Path, path: str) -> dict:
    """Read a file inside data_dir. Returns error dict if path is outside or missing."""
    try:
        p = Path(path)
        target = (data_dir / p).resolve() if not p.is_absolute() else p.resolve()
        allowed = data_dir.resolve()
        if not str(target).startswith(str(allowed)):
            return {"error": f"Access denied: path must be under {allowed}"}
        content = target.read_text()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"content": content}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except Exception as exc:
        return {"error": str(exc)}


_SYSTEM_PROMPT = (
    "You are a harness optimization agent for a construction bidding AI system. "
    "The system runs 5 bidding personalities in parallel on each job. You must improve "
    "the underperforming personalities by finding concrete evidence of why they lose.\n\n"
    "Use list_traces to find relevant tournaments, read_file to examine bid breakdowns "
    "in detail, and read the client profile for aggregate win rates and history.\n\n"
    "When you have sufficient evidence, output ONLY a valid JSON object mapping "
    "agent name to new prompt string. Include only the agents you were asked to improve."
)


def _run_agentic_proposer(
    *,
    data_dir: Path,
    client_id: str,
    underperforming: list[str],
    dominant_agent: str,
    dominant_rate: float,
    profile_path: Path,
) -> str:
    """
    Run an agentic loop with file-reading tools. Claude navigates trace files to
    gather diagnostic evidence, then proposes improved personality prompts.
    Returns raw text from Claude's final response (JSON string, possibly markdown-wrapped).
    """
    import anthropic

    initial_message = (
        f"Client: {client_id}\n"
        f"Dominant agent: {dominant_agent} ({dominant_rate:.0%} win rate)\n"
        f"Agents to improve: {', '.join(underperforming)}\n\n"
        f"Client profile path: {profile_path}\n\n"
        "Investigate why the underperforming agents lose by reading trace files, "
        "then propose improved personality prompts."
    )

    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": initial_message}]
    forced = False
    tool_call_count = 0

    while True:
        kwargs: dict = {
            "model": HARNESS_EVOLVER_MODEL,
            "max_tokens": 4096,
            "system": _SYSTEM_PROMPT,
            "messages": messages,
        }
        if forced:
            kwargs["tools"] = _TOOLS
            kwargs["tool_choice"] = {"type": "none"}
        elif tool_call_count < HARNESS_EVOLVER_MAX_TOOL_CALLS:
            kwargs["tools"] = _TOOLS

        response = client.messages.create(**kwargs)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text.strip()
            raise ValueError("Agentic proposer returned no text in final response")

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_call_count += 1
                    if block.name == "list_traces":
                        result = _handle_list_traces(data_dir, **block.input)
                    elif block.name == "read_file":
                        result = _handle_read_file(data_dir, block.input["path"])
                    else:
                        result = {"error": f"Unknown tool: {block.name}"}

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

            messages.append({"role": "user", "content": tool_results})

            if tool_call_count >= HARNESS_EVOLVER_MAX_TOOL_CALLS:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have enough context. "
                        "Output your proposed prompts now as a JSON object."
                    ),
                })
                forced = True

        else:
            raise ValueError(
                f"Agentic proposer: unexpected stop_reason '{response.stop_reason}'"
            )


async def evolve_harness(client_id: str) -> dict:
    """
    Read client diagnostic context, call Claude to propose improved prompts
    for underperforming agents, apply changes to tournament.py, and git commit.

    Returns a result dict with status: 'evolved' | 'skipped' | 'locked'.
    Raises ValueError on bad Claude response.
    """
    lock = _get_lock()
    if lock.locked():
        return {"status": "locked"}

    async with lock:
        from backend.agents.feedback_loop import _profile_path, ALL_AGENTS

        path = _profile_path(client_id)
        if not path.exists():
            return {"status": "skipped", "reason": "no profile found"}

        profile = json.loads(path.read_text())
        total = profile.get("stats", {}).get("total_tournaments", 0)

        if total < MIN_TOURNAMENTS:
            return {
                "status": "skipped",
                "reason": f"insufficient data ({total}/{MIN_TOURNAMENTS} tournaments)",
            }

        win_rates = profile.get("stats", {}).get("win_rate_by_agent", {})
        if not win_rates or max(win_rates.values()) <= DOMINANCE_THRESHOLD:
            return {
                "status": "skipped",
                "reason": "no dominance detected",
                "win_rates": win_rates,
            }

        dominant_agent = max(win_rates, key=win_rates.get)
        dominant_rate = win_rates[dominant_agent]
        underperforming = [a for a in ALL_AGENTS if a != dominant_agent]

        # ── Call Claude (agentic loop) ────────────────────────────────────────
        from backend.agents.feedback_loop import _profile_path as _fp
        data_dir = TOURNAMENT_PY.parent.parent / "data"
        raw = await asyncio.to_thread(
            _run_agentic_proposer,
            data_dir=data_dir,
            client_id=client_id,
            underperforming=underperforming,
            dominant_agent=dominant_agent,
            dominant_rate=dominant_rate,
            profile_path=_fp(client_id),
        )

        # ── Parse JSON response ───────────────────────────────────────────────
        try:
            proposed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if match:
                proposed = json.loads(match.group(1))
            else:
                raise ValueError(f"Claude returned non-JSON response: {raw[:300]}")

        # Only apply keys that are valid underperforming agents
        valid_proposed = {
            k: v
            for k, v in proposed.items()
            if k in ALL_AGENTS and k != dominant_agent
        }
        if not valid_proposed:
            raise ValueError(f"Claude returned no valid agent keys: {list(proposed.keys())}")

        # ── Apply to tournament.py ────────────────────────────────────────────
        source = TOURNAMENT_PY.read_text()
        for agent_name, new_prompt in valid_proposed.items():
            source = _replace_prompt_in_source(source, agent_name, new_prompt)
        TOURNAMENT_PY.write_text(source)

        # ── Git commit ────────────────────────────────────────────────────────
        gen = await asyncio.to_thread(_get_generation_number) + 1
        agent_list = ",".join(valid_proposed.keys())
        commit_msg = (
            f"harness: gen-{gen} — evolved {agent_list} "
            f"(dominant: {dominant_agent} at {dominant_rate:.0%})"
        )
        repo_root = TOURNAMENT_PY.parent.parent.parent
        commit_hash = await asyncio.to_thread(_git_commit, repo_root, TOURNAMENT_PY, commit_msg)

        return {
            "status": "evolved",
            "generation": gen,
            "evolved_agents": list(valid_proposed.keys()),
            "dominant_agent": dominant_agent,
            "dominant_win_rate": dominant_rate,
            "commit": commit_hash,
        }
