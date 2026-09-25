from __future__ import annotations

import io
from urllib.request import Request, urlopen

import pandas as pd

from swingtrading.models import Constituent

SP500_WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = "swingtrading/0.1 (personal research; overnight screener)"


def to_yahoo_symbol(symbol: str) -> str:
    """Wikipedia uses BRK.B; Yahoo uses BRK-B."""
    return symbol.strip().replace(".", "-")


def fetch_sp500(url: str = SP500_WIKI) -> list[Constituent]:
    html = _get(url)
    tables = pd.read_html(io.StringIO(html), flavor="lxml")
    if not tables:
        raise RuntimeError("Wikipedia returned no tables for S&P 500 constituents")
    raw = tables[0]
    symbol_col = _first_col(raw, ("symbol", "ticker"))
    name_col = _first_col(raw, ("security", "company", "name"))
    sector_col = _first_col(raw, ("gics sector", "sector"))
    out: list[Constituent] = []
    seen: set[str] = set()
    for _, row in raw.iterrows():
        symbol = str(row[symbol_col]).strip().upper()
        if not symbol or symbol == "NAN" or symbol in seen:
            continue
        seen.add(symbol)
        name = str(row[name_col]).strip() if name_col else symbol
        sector = str(row[sector_col]).strip() if sector_col else "Unknown"
        if sector.lower() == "nan":
            sector = "Unknown"
        out.append(
            Constituent(
                symbol=symbol,
                yahoo_symbol=to_yahoo_symbol(symbol),
                name=name,
                sector=sector,
            )
        )
    if len(out) < 400:
        raise RuntimeError(f"Expected ~500 S&P names, got {len(out)}")
    return out


def _get(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _first_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in candidates:
        if name in lowered:
            return lowered[name]
    return None
