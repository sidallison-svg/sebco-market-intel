"""
PDF export for Sebco Market Intel.

Renders a one-page market snapshot (HTML via Jinja2 -> PDF via WeasyPrint).

WeasyPrint needs native libraries (Pango / Cairo / GObject). On macOS those
are installed via Homebrew into /opt/homebrew/lib, which the dynamic loader
does NOT search by default and which cannot be added after the Python
process has started. So if the in-process import fails we transparently
render in a short-lived subprocess whose environment includes the Homebrew
lib path. This keeps it working no matter how Streamlit was launched, with
no setup required from the end user.

Add new report templates by dropping another file in templates/ and adding
a sibling render_* function.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_DIR = os.path.join(_DIR, "templates")

# Cached backend decision: None (undecided), or ("inproc", None) /
# ("subprocess", env_dict). Module-global so the (cost-bearing) detection
# runs once per Streamlit server process, not on every rerun.
_PDF_BACKEND: tuple | None = None


class PdfExportError(RuntimeError):
    """Raised when a PDF cannot be produced (e.g. WeasyPrint unavailable)."""


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------

EM_DASH = "—"


def _fmt(value, unit: str | None) -> str:
    """Format a metric value for the report. Missing -> em dash."""
    if value is None:
        return EM_DASH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return EM_DASH
    if unit == "percent":
        return f"{v:.1f}%"
    if unit == "dollar_per_sf":
        return f"${v:,.2f}"
    if unit == "sf":
        return f"{v:,.0f} SF"
    return f"{v:,.2f}"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _dedup_current(rows: list[sqlite3.Row]) -> dict:
    """Collapse to one value per (submarket, metric_type).

    Rows arrive ordered by confidence desc, id asc, so the first occurrence
    of a key is the most trustworthy. The DB legitimately holds several rows
    for the same metric (different parser strategies / a mis-typed unit), so
    this mirrors how Trends/Summary already deduplicate.
    """
    picked: dict[tuple, sqlite3.Row] = {}
    for r in rows:
        key = (r["submarket"], r["metric_type"])
        if key not in picked:
            picked[key] = r
    return picked


def _pick(picked: dict, submarket, *metric_types):
    """First present metric_type for a submarket -> (value, unit) or (None, None)."""
    for mt in metric_types:
        r = picked.get((submarket, mt))
        if r is not None:
            return r["value"], r["unit"]
    return None, None


def _fetch_snapshot_data(market: str, asset_class: str, quarter: str,
                         db_path: str) -> dict:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT submarket, metric_type, value, unit, confidence, id
            FROM metrics
            WHERE market = ? AND asset_class = ? AND quarter = ?
              AND period_type = 'current'
            ORDER BY confidence DESC, id ASC
            """,
            (market, asset_class, quarter),
        ).fetchall()
    finally:
        conn.close()

    picked = _dedup_current(rows)

    # 2x2 key metrics — market-wide row (submarket='') per metric. v2 stores
    # market-wide rows with submarket = '' (not NULL); a missing dimension is
    # the empty string, never NULL, so the UNIQUE constraint stays honest.
    key_specs = [
        ("Total Vacancy", ("total_vacancy_rate", "vacancy_rate")),
        ("Asking Rent (NNN)", ("asking_rent",)),
        ("Net Absorption", ("net_absorption",)),
        ("Total Inventory", ("total_inventory",)),
    ]
    key_metrics = []
    for label, mts in key_specs:
        val, unit = _pick(picked, "", *mts)
        key_metrics.append({"label": label, "value": _fmt(val, unit)})

    # Submarket table — alphabetical, market-wide ('') excluded.
    sub_names = sorted({
        sm for (sm, _mt) in picked.keys() if sm
    })
    submarkets = []
    for sm in sub_names:
        vac_v, vac_u = _pick(picked, sm, "vacancy_rate", "total_vacancy_rate")
        rent_v, rent_u = _pick(picked, sm, "asking_rent")
        abs_v, abs_u = _pick(picked, sm, "net_absorption")
        submarkets.append({
            "name": sm,
            "vacancy": _fmt(vac_v, vac_u),
            "rent": _fmt(rent_v, rent_u),
            "absorption": _fmt(abs_v, abs_u),
        })

    now = datetime.now()
    return {
        "market": market,
        "asset_class": (asset_class or "").title(),
        "quarter": quarter,
        "generated_date": now.strftime("%B %d, %Y"),
        "generated_ts": now.strftime("%Y-%m-%d %H:%M"),
        "key_metrics": key_metrics,
        "submarkets": submarkets,
    }


# ---------------------------------------------------------------------------
# HTML -> PDF (with macOS subprocess fallback)
# ---------------------------------------------------------------------------

def _homebrew_lib_dirs() -> list[str]:
    dirs: list[str] = []
    try:
        out = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if out:
            dirs.append(os.path.join(out, "lib"))
    except Exception:
        pass
    dirs += ["/opt/homebrew/lib", "/usr/local/lib"]
    seen, out_dirs = set(), []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out_dirs.append(d)
    return out_dirs


def _subprocess_env() -> dict:
    env = os.environ.copy()
    parts = _homebrew_lib_dirs()
    cur = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if cur:
        parts.append(cur)
    parts += ["/usr/local/lib", "/usr/lib"]
    env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
        dict.fromkeys(p for p in parts if p)
    )
    return env


def _render_subprocess(html: str, env: dict) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "in.html")
        out_path = os.path.join(td, "out.pdf")
        with open(in_path, "w", encoding="utf-8") as f:
            f.write(html)
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__),
             "__weasyprint_worker__", in_path, out_path],
            env=env, capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise PdfExportError(
                "WeasyPrint subprocess failed:\n" + (proc.stderr or "")[-800:]
            )
        with open(out_path, "rb") as f:
            return f.read()


@contextlib.contextmanager
def _silence_stderr():
    """Suppress fd-level stderr (WeasyPrint's import-failure banner is
    written to fd 2 directly, so redirect_stderr alone won't catch it)."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def _try_inproc() -> bool:
    try:
        with _silence_stderr(), contextlib.redirect_stderr(io.StringIO()):
            from weasyprint import HTML  # noqa: F401
            HTML(string="<p>x</p>").write_pdf()
        return True
    except Exception:
        return False


def _try_subprocess(env: dict) -> bool:
    try:
        _render_subprocess("<p>x</p>", env)
        return True
    except Exception:
        return False


def _detect_backend() -> tuple:
    """Decide once how to render. Returns ('inproc', None) or
    ('subprocess', env). Raises PdfExportError if neither works.

    On macOS the in-process import almost never works under a normally
    launched Streamlit (Homebrew libs aren't on the loader path and that
    can't be fixed post-launch), so try the subprocess first there and
    skip the noisy failing import.
    """
    if sys.platform == "darwin":
        env = _subprocess_env()
        if _try_subprocess(env):
            return ("subprocess", env)
        if _try_inproc():
            return ("inproc", None)
    else:
        if _try_inproc():
            return ("inproc", None)
        env = _subprocess_env()
        if _try_subprocess(env):
            return ("subprocess", env)

    raise PdfExportError(
        "WeasyPrint could not load its native libraries (Pango/Cairo). "
        "On macOS run: brew install pango cairo — then restart the app. "
        "Until then, use the CSV export options instead."
    )


def _html_to_pdf(html: str) -> bytes:
    global _PDF_BACKEND
    if _PDF_BACKEND is None:
        _PDF_BACKEND = _detect_backend()
    mode, env = _PDF_BACKEND
    if mode == "inproc":
        from weasyprint import HTML
        return HTML(string=html, base_url=_DIR).write_pdf()
    return _render_subprocess(html, env)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def snapshot_filename(market: str, asset_class: str, quarter: str) -> str:
    """sebco_{market}_{asset_class}_{quarter}.pdf, lowercased, safe."""
    def slug(s: str) -> str:
        s = (s or "").lower().strip()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        return s.strip("_")
    return f"sebco_{slug(market)}_{slug(asset_class)}_{slug(quarter)}.pdf"


def render_market_snapshot(market: str, asset_class: str, quarter: str,
                           db_path: str) -> bytes:
    """Return PDF bytes for the one-page market snapshot.

    asset_class is part of the report identity (a market such as Seattle
    has separate industrial / office / multifamily reports), so it is
    required even though the original stub omitted it.
    """
    data = _fetch_snapshot_data(market, asset_class, quarter, db_path)
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("market_snapshot.html").render(**data)
    return _html_to_pdf(html)


# ---------------------------------------------------------------------------
# Market Overview report (Sebco portfolio vs. market, one landscape page)
# ---------------------------------------------------------------------------

_ARROW_UP, _ARROW_DOWN, _ARROW_FLAT = "▲", "▼", "●"
_RED, _GREEN, _GRAY = "#C0392B", "#1E8449", "#9CA3AF"


def _ov_lease_label(lease_type) -> str:
    if lease_type == "NNN":
        return "NNN"
    if lease_type == "industrial_gross":
        return "industrial-gross"
    return ""


def _q_label(quarter: str | None) -> str:
    """'1Q 2026' -> 'Q1 2026'. Falls back to the raw string."""
    if not quarter:
        return ""
    m = re.match(r"(\d)Q\s*(\d{4})", str(quarter).strip())
    return f"Q{m.group(1)} {m.group(2)}" if m else str(quarter)


def _ov_pick(conn, name, metric_types, period_type, portfolio):
    """Best DB row for a Sebco portfolio market + metric + period.

    Resolves `name` through the portfolio's data_source alias map (via
    utils.data_query_keys) so submarket-backed markets — Kent Valley ->
    Seattle/Southend, Marysville -> Seattle/Northend — find their data the
    same way the Pulse page does. `metric_types` may be a single string or
    an ordered list (first match wins, e.g. total_vacancy_rate then the
    Kidder-style vacancy_rate). Submarket aliases are tried in priority
    order; within a hit, higher confidence then most recent wins.
    """
    from utils import data_query_keys

    if isinstance(metric_types, str):
        metric_types = [metric_types]
    db_market, submarkets = data_query_keys(name, portfolio)
    for mt in metric_types:
        for sub in submarkets:
            row = conn.execute(
                """
                SELECT value, unit, lease_type, quarter, period_date
                FROM metrics
                WHERE metric_type = ? AND period_type = ?
                  AND LOWER(market) = LOWER(?) AND submarket = ?
                  AND asset_class IN ('industrial', 'overall')
                ORDER BY confidence DESC, period_date DESC
                LIMIT 1
                """,
                (mt, period_type, db_market, sub),
            ).fetchone()
            if row is not None and row["value"] is not None:
                return row
    return None


def _num(row):
    if row is None or row["value"] is None:
        return None
    try:
        return float(row["value"])
    except (TypeError, ValueError):
        return None


def _abbr_sf(v: float) -> str:
    """Square-footage with M/k abbreviation: 2,580,000 -> '2.58M sf'."""
    v = abs(v)
    if v >= 1_000_000:
        return f"{v / 1_000_000:,.2f}M sf"
    if v >= 1_000:
        return f"{v / 1_000:,.0f}k sf"
    return f"{v:,.0f} sf"


def _missing() -> dict:
    return {"value": EM_DASH, "meta": "", "arrow": "", "color": "",
            "tint": False, "missing": True}


def _vac_cell(cur, prev) -> dict:
    v = _num(cur)
    if v is None:
        return _missing()
    p = _num(prev)
    if p is None or abs(v - p) <= 0.1:
        arrow, color = _ARROW_FLAT, _GRAY
    elif v > p:
        arrow, color = _ARROW_UP, _RED     # vacancy up = bad
    else:
        arrow, color = _ARROW_DOWN, _GREEN
    return {"value": f"{v:.1f}%", "meta": "", "arrow": arrow, "color": color,
            "tint": True, "missing": False}


def _rent_cell(cur, prev) -> dict:
    v = _num(cur)
    if v is None:
        return _missing()
    p = _num(prev)
    if p is None or abs(v - p) < 0.01:
        arrow, color = _ARROW_FLAT, _GRAY
    elif v > p:
        arrow, color = _ARROW_UP, _GREEN   # rent up = good
    else:
        arrow, color = _ARROW_DOWN, _RED
    return {"value": f"${v:,.2f}", "meta": _ov_lease_label(cur["lease_type"]),
            "arrow": arrow, "color": color, "tint": True, "missing": False}


def _sebco_cell(pf: dict) -> dict:
    r = pf.get("sebco_asking_rent")
    if r is None:
        return _missing()
    try:
        rv = float(r)
    except (TypeError, ValueError):
        return _missing()
    return {"value": f"${rv:,.2f}", "meta": _ov_lease_label(pf.get("lease_type")),
            "arrow": "", "color": "", "tint": False, "missing": False}


def _abs_cell(cur) -> dict:
    v = _num(cur)
    if v is None:
        return _missing()
    sign = "+" if v >= 0 else "-"
    if abs(v) < 1000:
        arrow, color = _ARROW_FLAT, _GRAY
    elif v > 0:
        arrow, color = _ARROW_UP, _GREEN
    else:
        arrow, color = _ARROW_DOWN, _RED
    return {"value": f"{sign}{_abbr_sf(v)}", "meta": _q_label(cur["quarter"]),
            "arrow": arrow, "color": color, "tint": True, "missing": False}


def _pipe_cell(uc, planned) -> dict:
    ucv = _num(uc)
    pv = _num(planned)
    if ucv is not None and ucv >= 50_000:
        value, meta = _abbr_sf(ucv), "U/C"
    elif pv is not None and pv >= 50_000:
        value, meta = _abbr_sf(pv), "Proposed"
    elif ucv is not None or pv is not None:
        value, meta = "Limited", ""
    else:
        return _missing()
    return {"value": value, "meta": meta, "arrow": "", "color": "",
            "tint": False, "missing": False}


def _overview_quarter(conn) -> str:
    """Quarter for the title/footer, derived from the latest current
    industrial metric; falls back to today's calendar quarter."""
    row = conn.execute(
        "SELECT quarter FROM metrics WHERE asset_class = 'industrial' "
        "AND period_type = 'current' AND period_date <> '' "
        "ORDER BY period_date DESC LIMIT 1"
    ).fetchone()
    if row and row["quarter"]:
        ql = _q_label(row["quarter"])
        if ql:
            return ql
    now = datetime.now()
    return f"Q{(now.month - 1) // 3 + 1} {now.year}"


_VACANCY_MTS = ["total_vacancy_rate", "vacancy_rate"]
_ABSORPTION_MTS = ["net_absorption", "ytd_net_absorption"]


def _key_actions(stats: list[dict]) -> list[str]:
    """A few factual portfolio call-outs derived from the real numbers
    (highest vacancy, tightest market, best Sebco rent position)."""
    actions: list[str] = []
    with_vac = [s for s in stats if s["vac"] is not None]
    if with_vac:
        hi = max(with_vac, key=lambda s: s["vac"])
        lo = min(with_vac, key=lambda s: s["vac"])
        actions.append(
            f"Highest vacancy: {hi['name']} at {hi['vac']:.1f}% — watch "
            f"leasing exposure."
        )
        if lo["name"] != hi["name"]:
            actions.append(
                f"Tightest market: {lo['name']} at {lo['vac']:.1f}%."
            )
    spreads = [
        (s["name"], s["sebco"], s["market"])
        for s in stats
        if s["sebco"] is not None and s["market"] is not None
    ]
    if spreads:
        name, sebco, market = min(spreads, key=lambda t: t[1] - t[2])
        gap = sebco - market
        rel = "below" if gap < 0 else "above"
        actions.append(
            f"Best rent position: {name}, Sebco ${sebco:.2f} vs market "
            f"${market:.2f} ({abs(gap):.2f} {rel})."
        )
    return actions


def _fetch_overview_data(db_path: str) -> dict:
    from utils import SEBCO_PORTFOLIO_ORDER, load_sebco_portfolio

    portfolio = load_sebco_portfolio()
    names = [n for n in SEBCO_PORTFOLIO_ORDER if n in portfolio]
    for n in portfolio:
        if n not in names:
            names.append(n)
    if not names:
        names = list(SEBCO_PORTFOLIO_ORDER)

    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        quarter = _overview_quarter(conn)
        rows = []
        stats = []  # raw numbers for the Key Portfolio Actions block
        for name in names:
            pf = portfolio.get(name, {})
            bits = []
            bc = pf.get("building_count")
            if bc is not None:
                bits.append(f"{bc} bldg{'s' if bc != 1 else ''}")
            tsf = pf.get("total_sf")
            if tsf:
                bits.append(f"{tsf / 1000:,.0f}k sf")

            vac_cur = _ov_pick(conn, name, _VACANCY_MTS, "current", portfolio)
            rent_cur = _ov_pick(conn, name, "asking_rent", "current", portfolio)
            rows.append({
                "name": name.upper(),
                "sub_meta": ("(" + ", ".join(bits) + ")") if bits else "",
                "vacancy": _vac_cell(
                    vac_cur,
                    _ov_pick(conn, name, _VACANCY_MTS, "prior_quarter",
                             portfolio),
                ),
                "market_rent": _rent_cell(
                    rent_cur,
                    _ov_pick(conn, name, "asking_rent", "prior_quarter",
                             portfolio),
                ),
                "sebco_rent": _sebco_cell(pf),
                "absorption": _abs_cell(
                    _ov_pick(conn, name, _ABSORPTION_MTS, "current", portfolio)
                ),
                "pipeline": _pipe_cell(
                    _ov_pick(conn, name, "under_construction", "current",
                             portfolio),
                    _ov_pick(conn, name, "planned_construction", "current",
                             portfolio),
                ),
            })
            stats.append({
                "name": name.title(),
                "vac": _num(vac_cur),
                "market": _num(rent_cur),
                "sebco": pf.get("sebco_asking_rent"),
            })
    finally:
        conn.close()

    total_b = sum(portfolio.get(n, {}).get("building_count") or 0 for n in names)
    total_sf = sum(portfolio.get(n, {}).get("total_sf") or 0 for n in names)
    sub_bits = []
    if total_b:
        sub_bits.append(f"{total_b} buildings")
    if total_sf:
        sub_bits.append(f"{total_sf / 1000:,.0f}k sf")
    subtitle = " · ".join(sub_bits)
    if subtitle:
        subtitle += f" across {len(names)} Sebco markets"
    else:
        subtitle = f"{len(names)} Sebco markets"

    return {
        "title": f"{quarter.upper()} INDUSTRIAL MARKET DASHBOARD",
        "subtitle": subtitle,
        "footer_quarter": quarter,
        "current_month_year": datetime.now().strftime("%B %Y"),
        "rows": rows,
        "key_actions": _key_actions(stats),
    }


def market_overview_filename() -> str:
    return f"sebco_market_overview_{datetime.now():%Y-%m-%d}.pdf"


def render_market_overview_html(db_path: str) -> str:
    """The Market Overview as an HTML string (used for the live preview)."""
    data = _fetch_overview_data(db_path)
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("market_overview.html").render(**data)


def render_market_overview(db_path: str) -> bytes:
    """One-page landscape Market Overview PDF (bytes)."""
    return _html_to_pdf(render_market_overview_html(db_path))


# ---------------------------------------------------------------------------
# Subprocess worker entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__" and len(sys.argv) >= 4 \
        and sys.argv[1] == "__weasyprint_worker__":
    from weasyprint import HTML

    HTML(filename=sys.argv[2]).write_pdf(sys.argv[3])
    sys.exit(0)
