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
            return r["metric_value"], r["unit"]
    return None, None


def _fetch_snapshot_data(market: str, asset_class: str, quarter: str,
                         db_path: str) -> dict:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT submarket, metric_type, metric_value, unit, confidence, id
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

    # 2x2 key metrics — market-wide row (submarket IS NULL) per metric.
    key_specs = [
        ("Total Vacancy", ("total_vacancy_rate", "vacancy_rate")),
        ("Asking Rent (NNN)", ("asking_rent",)),
        ("Net Absorption", ("net_absorption",)),
        ("Total Inventory", ("total_inventory",)),
    ]
    key_metrics = []
    for label, mts in key_specs:
        val, unit = _pick(picked, None, *mts)
        key_metrics.append({"label": label, "value": _fmt(val, unit)})

    # Submarket table — alphabetical, market-wide (None) excluded.
    sub_names = sorted({
        sm for (sm, _mt) in picked.keys() if sm is not None
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
# Subprocess worker entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__" and len(sys.argv) >= 4 \
        and sys.argv[1] == "__weasyprint_worker__":
    from weasyprint import HTML

    HTML(filename=sys.argv[2]).write_pdf(sys.argv[3])
    sys.exit(0)
