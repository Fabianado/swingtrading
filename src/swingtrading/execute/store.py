from __future__ import annotations

from datetime import date
from pathlib import Path

from swingtrading.models import ExecutionState, Playbook, TradePlan


def playbook_path(out_dir: Path, as_of: date) -> Path:
    return Path(out_dir) / f"{as_of.isoformat()}.json"


def execution_paths(out_dir: Path, as_of: date) -> tuple[Path, Path]:
    stem = Path(out_dir) / f"{as_of.isoformat()}.execution"
    return stem.with_suffix(".execution.json"), stem.with_suffix(".execution.md")


def list_playbook_files(out_dir: Path) -> list[Path]:
    root = Path(out_dir)
    if not root.exists():
        return []
    return sorted(
        p
        for p in root.glob("*.json")
        if not p.name.endswith(".execution.json")
        and p.name != "positions.json"
        and _is_date_json(p)
    )


def _is_date_json(path: Path) -> bool:
    name = path.stem
    return len(name) == 10 and name[4] == "-" and name[7] == "-"


def load_playbook(out_dir: Path, as_of: date | None = None) -> Playbook:
    if as_of is not None:
        path = playbook_path(out_dir, as_of)
        if not path.exists():
            raise FileNotFoundError(f"No playbook at {path}. Run without tickers first.")
        return Playbook.model_validate_json(path.read_text(encoding="utf-8"))
    files = list_playbook_files(out_dir)
    if not files:
        raise FileNotFoundError(f"No playbook JSON in {out_dir}. Run without tickers first.")
    return Playbook.model_validate_json(files[-1].read_text(encoding="utf-8"))


def playbook_risk_usd(playbook: Playbook, fallback: float) -> float:
    if playbook.risk_usd is not None:
        return float(playbook.risk_usd)
    if playbook.picks:
        return float(playbook.picks[0].risk_usd)
    return fallback


def playbook_symbols(playbook: Playbook) -> list[str]:
    """Shortlist symbols in playbook order (execute re-sorts by confidence)."""
    return [plan.symbol for plan in playbook.picks]


def match_plan(playbook: Playbook, ticker: str) -> TradePlan:
    aliases = _aliases(ticker)
    for plan in playbook.picks:
        if plan.symbol.upper() in aliases or _aliases(plan.symbol) & aliases:
            return plan
    available = ", ".join(p.symbol for p in playbook.picks) or "(empty shortlist)"
    raise KeyError(f"{ticker} is not in the playbook shortlist. Picks: {available}")


def _aliases(ticker: str) -> set[str]:
    raw = ticker.strip().upper()
    return {raw, raw.replace(".", "-"), raw.replace("-", ".")}


def save_execution(state: ExecutionState, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = execution_paths(out_dir, state.as_of)
    json_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_execution(state), encoding="utf-8")
    return json_path, md_path


def render_execution(state: ExecutionState) -> str:
    lines = [
        f"# Execution — {state.as_of.isoformat()}",
        "",
        f"- Risk per idea: **${state.risk_usd:.0f}**",
        f"- Max new positions: **{state.max_new_positions}**",
        f"- Requested: {', '.join(state.tickers) or '(none)'}",
        f"- Opened: {', '.join(state.opened) or '(none)'}",
        f"- Canceled: {', '.join(state.canceled) or '(none)'}",
        f"- Invalidated: {', '.join(state.invalidated) or '(none)'}",
        f"- Skipped: {', '.join(state.skipped) or '(none)'}",
        "",
        "## Decisions",
        "",
    ]
    if not state.decisions:
        lines.append("No actions.")
    for item in state.decisions:
        extra = f" ({item.qty} shares)" if item.qty else ""
        lines.append(f"- **{item.symbol}** {item.action}{extra}: {item.reason}")
    return "\n".join(lines).rstrip() + "\n"
