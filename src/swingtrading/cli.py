from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from dotenv import load_dotenv

from swingtrading.config import Settings
from swingtrading.execute.ledger import due_fills, load_ledger, session_date
from swingtrading.execute.moc import run_moc_job
from swingtrading.execute.session import execute_tickers
from swingtrading.execute.store import load_playbook, playbook_symbols
from swingtrading.pipeline import ingest, run_and_write

app = typer.Typer(help="Overnight S&P 500 swing playbook (research + optional TWS execute).")


def _parse_as_of(value: str | None) -> date | None:
    if value is None or value.strip().lower() in {"", "today"}:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _settings(
    *,
    risk_usd: float | None,
    cache_dir: Path | None,
    out_dir: Path | None,
    live: bool,
    tws_host: str | None,
    tws_port: int | None,
) -> Settings:
    settings = Settings()
    if risk_usd is not None:
        settings.risk_usd = risk_usd
    if cache_dir is not None:
        settings.cache_dir = cache_dir
    if out_dir is not None:
        settings.out_dir = out_dir
    if live:
        settings.tws_port = 7496
    if tws_host is not None:
        settings.tws_host = tws_host
    if tws_port is not None:
        settings.tws_port = tws_port
    return settings


@app.command()
def run(
    tickers: list[str] | None = typer.Argument(
        None,
        help="If set, automate these shortlist symbols via TWS (second run). "
        "Omit to generate the overnight playbook.",
    ),
    as_of: str | None = typer.Option(None, help="YYYY-MM-DD of last completed session, or 'today'."),
    skip_ai: bool = typer.Option(False, help="Quant-only dry run; no xAI tokens."),
    offline: bool = typer.Option(False, help="Use Parquet cache only; do not hit Wikipedia/Yahoo."),
    risk_usd: float | None = typer.Option(
        None,
        help="Dollars risked per idea. First run default 500. "
        "Second run: omit to reuse the playbook value.",
    ),
    max_new_positions: int | None = typer.Option(
        None,
        help="Second run only: cancel leftover entries after this many new fills.",
    ),
    live: bool = typer.Option(False, help="TWS live port 7496 (default is paper 7497)."),
    now: bool = typer.Option(
        False,
        "--now",
        help="Second run only: do not wait for the NYSE 9:30 ET open.",
    ),
    skip_moc: bool = typer.Option(
        False,
        "--skip-moc",
        help="Second run only: do not wait for 15:45 ET to flatten due time-stops.",
    ),
    all_picks: bool = typer.Option(
        False,
        "--all",
        help="Second run: execute every symbol on the saved playbook shortlist.",
    ),
    tws_host: str | None = typer.Option(None, help="TWS / LYNX Gateway host (default 127.0.0.1)."),
    tws_port: int | None = typer.Option(None, help="Override TWS socket port."),
    cache_dir: Path | None = typer.Option(None),
    out_dir: Path | None = typer.Option(None),
) -> None:
    """Screen the S&P 500, or execute saved shortlist tickers through TWS."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()
    settings = _settings(
        risk_usd=risk_usd,
        cache_dir=cache_dir,
        out_dir=out_dir,
        live=live,
        tws_host=tws_host,
        tws_port=tws_port,
    )
    names = [t for t in (tickers or []) if t.strip()]
    if all_picks and names:
        raise typer.BadParameter("Pass either --all or ticker symbols, not both.")
    if all_picks:
        try:
            book = load_playbook(settings.out_dir, _parse_as_of(as_of))
        except FileNotFoundError as exc:
            raise typer.BadParameter(str(exc)) from exc
        names = playbook_symbols(book)
        if not names:
            raise typer.BadParameter("Playbook shortlist is empty; nothing to execute.")
    if names:
        try:
            state = execute_tickers(
                settings,
                names,
                risk_usd=risk_usd,
                max_new_positions=max_new_positions,
                as_of=_parse_as_of(as_of),
                wait_for_open=not now,
                wait_for_moc=not skip_moc,
            )
        except (FileNotFoundError, KeyError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            f"Execution as-of {state.as_of}: opened {len(state.opened)}, "
            f"canceled {len(state.canceled)}, invalidated {len(state.invalidated)}, "
            f"skipped {len(state.skipped)}"
        )
        for item in state.decisions:
            extra = f" {item.qty} sh" if item.qty else ""
            typer.echo(f"- {item.symbol} {item.action}{extra}: {item.reason}")
        return

    playbook, json_path, md_path = run_and_write(
        settings,
        as_of=_parse_as_of(as_of),
        skip_ai=skip_ai,
        refresh=not offline,
    )
    typer.echo(f"Wrote {len(playbook.picks)} pick(s) to {md_path} and {json_path}")
    for i, plan in enumerate(playbook.picks, start=1):
        typer.echo(
            f"{i}. {plan.symbol} {plan.side.value} {plan.setup_type.value} "
            f"{plan.qty} shares @ {plan.entry_price:.2f} stop {plan.stop_price:.2f} "
            f"tgt {plan.target_price:.2f} conf {plan.confidence:.1f}"
        )


@app.command()
def moc(
    now: bool = typer.Option(
        False,
        "--now",
        help="Do not wait for 15:45 ET; flatten due ledger fills immediately.",
    ),
    live: bool = typer.Option(False, help="TWS live port 7496 (default is paper 7497)."),
    tws_host: str | None = typer.Option(None, help="TWS / LYNX Gateway host (default 127.0.0.1)."),
    tws_port: int | None = typer.Option(None, help="Override TWS socket port."),
    out_dir: Path | None = typer.Option(None),
) -> None:
    """Flatten program-owned fills that have reached the 5-session time stop.

    Only rows in out/positions.json are touched (qty-scoped MOC). Manual
    Lynx positions are ignored.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()
    settings = _settings(
        risk_usd=None,
        cache_dir=None,
        out_dir=out_dir,
        live=live,
        tws_host=tws_host,
        tws_port=tws_port,
    )
    before = load_ledger(settings.out_dir)
    today = session_date(datetime.now(ZoneInfo(settings.rth_timezone)), settings.rth_timezone)
    pending = due_fills(before, today)
    if not pending:
        typer.echo("No program fills are due for a time-stop MOC.")
        return
    refs = {row.order_ref for row in pending}
    try:
        ledger = run_moc_job(
            settings,
            wait=not now,
            ignore_window=now,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Time-stop MOC: {len(pending)} due fill(s)")
    for row in ledger.fills:
        if row.order_ref in refs:
            typer.echo(f"- {row.symbol} {row.status} {row.qty} sh ({row.order_ref})")


@app.command()
def refresh(
    cache_dir: Path | None = typer.Option(None),
) -> None:
    """Download constituents, OHLCV, and earnings into the Parquet cache."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()
    settings = Settings()
    if cache_dir is not None:
        settings.cache_dir = cache_dir
    constituents, ohlcv, earnings = ingest(settings, refresh=True)
    typer.echo(
        f"Cached {len(constituents)} constituents, {len(ohlcv)} bars, "
        f"{sum(1 for v in earnings.values() if v)} earnings dates."
    )


if __name__ == "__main__":
    app()
