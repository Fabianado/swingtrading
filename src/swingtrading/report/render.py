from __future__ import annotations

from pathlib import Path

from swingtrading.models import Playbook, Side, TradePlan


def write_playbook(playbook: Playbook, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = playbook.as_of.isoformat()
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(playbook.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(playbook), encoding="utf-8")
    return json_path, md_path


def render_markdown(playbook: Playbook) -> str:
    lines = [
        f"# S&P 500 swing playbook — {playbook.as_of.isoformat()}",
        "",
        "Research-only. Not financial advice. v1 does not place orders.",
        "",
        f"- Universe after ingest: **{playbook.universe_size}**",
        f"- Passed hard filters: **{playbook.filtered_size}**",
        f"- Setups scored ≥ threshold: **{playbook.scored_size}**",
        f"- AI skipped: **{playbook.skip_ai}** (spent ~${playbook.ai_spent_usd:.2f})",
        f"- Generated: {playbook.generated_at}",
        "",
    ]
    if playbook.notes:
        lines.extend([playbook.notes, ""])
    if not playbook.picks:
        lines.append("No names cleared the screen for the next session.")
        return "\n".join(lines) + "\n"

    lines.append("Ranked by confidence. Use DAY orders in the next regular US session only.")
    lines.append("")
    lines.append("### Shares to trade")
    lines.append("")
    for i, plan in enumerate(playbook.picks, start=1):
        side = "LONG" if plan.side is Side.BUY else "SHORT"
        lines.append(
            f"{i}. **{plan.symbol}** {side}: **{plan.qty} shares** "
            f"(${plan.risk_usd:.0f} risk per idea, stop {abs(plan.entry_price - plan.stop_price):.2f})"
        )
    lines.append("")
    for i, plan in enumerate(playbook.picks, start=1):
        lines.extend(_plan_section(i, plan))
        lines.append("")
    if playbook.discarded:
        lines.append("## Vetoed / dropped from the shortlist")
        lines.append("")
        for plan in playbook.discarded:
            reason = ""
            if plan.ai and plan.ai.veto:
                reason = plan.ai.veto_reason or "veto"
            lines.append(f"- **{plan.symbol}** ({plan.side.value}): {reason or plan.rationale}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _plan_section(rank: int, plan: TradePlan) -> list[str]:
    side = "LONG" if plan.side is Side.BUY else "SHORT"
    action = "Buy" if plan.side is Side.BUY else "Sell short"
    exit_action = "Sell" if plan.side is Side.BUY else "Buy to cover"
    r_mult = plan.round_trip_r()
    lines = [
        f"## {rank}. {plan.symbol} — {side} ({plan.setup_type.value}) — {plan.qty} shares",
        "",
        f"**{plan.name or plan.symbol}** · {plan.sector} · confidence **{plan.confidence:.1f}** "
        f"(quant {plan.quant_score:.1f})",
        "",
        plan.rationale,
        "",
        f"**Shares to trade: {plan.qty}**  "
        f"(sized so a stop is about ${plan.risk_usd:.0f}; "
        f"stop distance {abs(plan.entry_price - plan.stop_price):.2f}).",
        "",
        "### Open next session if",
        "",
        f"- Submit a **{plan.entry_type.value} {action}** of **{plan.qty} shares** at **{plan.entry_price:.2f}** "
        f"({plan.tif}), SMART/{plan.currency}, whole shares only.",
        f"- Risk: ~${plan.risk_usd:.0f} if the stop at {plan.stop_price:.2f} is hit.",
        f"- {plan.skip_if.description}",
        "",
        "### Close if any of",
        "",
        f"- **Stop:** {exit_action} **STP** at **{plan.stop_price:.2f}** (attach as child of the entry).",
        f"- **Target:** {exit_action} **LMT** at **{plan.target_price:.2f}** (~{r_mult:.1f}R).",
        f"- **Time stop:** flatten at the close of session **{plan.time_stop_sessions}** "
        "if neither stop nor target has filled (15:45 ET MOC on this program's ledger row only).",
        "",
        "Retail TWS bracket later: parent entry + child target LMT + child stop STP. "
        "Do not use FIX, IBALGOs, or the Client Portal Web API.",
    ]
    if plan.ai:
        lines.extend(
            [
                "",
                "### AI overlay",
                "",
                f"- Sentiment: {plan.ai.sentiment:+.2f} · event risk: {plan.ai.event_risk} · "
                f"delta {plan.ai.confidence_delta:+.0f}",
            ]
        )
        if plan.ai.sources:
            lines.append("- Sources: " + "; ".join(plan.ai.sources[:5]))
    return lines
