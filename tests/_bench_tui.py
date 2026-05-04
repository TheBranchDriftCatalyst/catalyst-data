"""Rich-based two-pane TUI for the benchmark harness run loop.

Provides:
- ``BenchLiveUI``  — context manager wrapping ``rich.Live`` with header/table/log layout
- ``_render_header`` — builds the pinned header Panel
- ``_render_table``  — builds the per-model status Table
- ``_render_log``    — builds the scrolling log Panel
- ``DequeHandler``   — ``logging.Handler`` that pushes records to a deque

Non-TTY: when stderr is not a TTY (CI / Tilt pipe), ``BenchLiveUI`` operates
in no-op mode and all log lines fall through to plain stderr writes so the
file artifact is unchanged.

Import surface consumed by ``benchmark_harness.py``:
    from tests._bench_tui import BenchLiveUI, DequeHandler
"""

from __future__ import annotations

import collections
import logging
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# Rich imports — all guarded so that a missing rich installation degrades
# gracefully to the non-TTY path.
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False

# Max lines kept in the scrolling log deque. Bumped from 30 → 1000 so users
# can scroll their terminal back through history (rich.Live is one-way; for
# in-pane scrolling the panel auto-tails the most recent lines that fit).
# Override via BENCH_TUI_LOG_BUFFER if a run produces an unusually fat log.
import os as _os

_LOG_MAXLEN = int(_os.environ.get("BENCH_TUI_LOG_BUFFER", "1000"))

# Synthwave palette — mirrors _ansi_palette() from benchmark_harness.py but as
# rich markup strings so we can use them inside rich renderables.
_M = "[bold magenta]"  # synthwave magenta
_C = "[bold cyan]"  # neon cyan
_Y = "[bold yellow]"  # CRT yellow
_G = "[bold green]"  # signal green
_K = "[dim white]"  # zinc neutral
_R = "[bold red]"  # alert red
_E = "[white]"  # eggshell default
_X = "[/]"  # reset (end tag, used selectively)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _tier_style(tier: str) -> str:
    """Map tier label to a rich colour markup open-tag."""
    return {
        "ENC": "[bold cyan]",
        "SPEC": "[bold magenta]",
        "CLOUD": "[bold yellow]",
        "T1": "[bold green]",
        "T2": "[green]",
        "LLM": "[white]",
    }.get(tier, "[white]")


def _status_markup(status: str) -> str:
    """Render a status string as coloured markup."""
    if status in ("ok", "cached"):
        return f"[bold green]✓ {status}[/]"
    if status == "running":
        return "[bold cyan]⟳ running[/]"
    if status == "queued":
        return "[dim white]◌ queued[/]"
    if status in ("skip", "no-endpt"):
        return f"[dim yellow]{status}[/]"
    if status in ("FAIL", "error"):
        return f"[bold red]✗ {status}[/]"
    return f"[white]{status}[/]"


def _render_header(run_id: str, pipeline: str, models: list[Any], sample_cap: str) -> Panel:
    """Build the pinned header panel with run metadata."""
    # Coerce to str so Text.assemble() doesn't choke on None / enum inputs.
    run_id = str(run_id) if run_id is not None else "(unknown)"
    pipeline = str(pipeline) if pipeline is not None else "(default)"
    sample_cap = str(sample_cap) if sample_cap is not None else "(unset)"

    n_enc = sum(1 for m in models if "encoder" in m.tags)
    n_cloud = sum(1 for m in models if "cloud" in m.tags)
    n_local_llm = sum(1 for m in models if "cloud" not in m.tags and "encoder" not in m.tags)

    run_short = run_id[:40] + ("…" if len(run_id) > 40 else "")
    line1 = Text.assemble(
        ("RUN  ", "bold white"),
        (run_short, "bold cyan"),
        ("    pipeline: ", "dim white"),
        (pipeline, "bold magenta"),
    )
    line2 = Text.assemble(
        ("models ", "dim white"),
        (str(len(models)), "bold white"),
        ("  ·  ", "dim white"),
        (f"{n_enc} enc", "bold cyan"),
        ("  ·  ", "dim white"),
        (f"{n_local_llm} local-llm", "white"),
        ("  ·  ", "dim white"),
        (f"{n_cloud} cloud", "bold yellow"),
        ("   sample cap: ", "dim white"),
        (sample_cap, "bold yellow"),
    )
    content = Text.assemble(line1, "\n", line2)
    return Panel(content, style="bold magenta", padding=(0, 1))


def _render_table(model_states: dict[str, dict]) -> Table:
    """Build the live-updating per-model status table from current state."""
    table = Table(
        show_header=True,
        header_style="bold magenta",
        border_style="dim magenta",
        show_lines=False,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", style="dim white", width=3, justify="right")
    table.add_column("model", style="bold white", min_width=22, max_width=28)
    table.add_column("tier", min_width=6, max_width=8)
    table.add_column("status", min_width=12)
    table.add_column("mentions", justify="right", min_width=8)
    table.add_column("spo", justify="right", min_width=5)
    table.add_column("time", justify="right", min_width=7)
    table.add_column("tok/s", justify="right", min_width=6)
    table.add_column("calls", justify="right", min_width=5)
    table.add_column("retry", justify="right", min_width=5)
    table.add_column("err", justify="right", min_width=4)

    for idx, (name, st) in enumerate(model_states.items(), 1):
        tier = st.get("tier", "LLM")
        tier_col = _tier_style(tier)
        status_col = _status_markup(st.get("status", "queued"))

        if st.get("status") in ("ok", "cached", "FAIL", "error"):
            mentions = str(st.get("mentions", "—"))
            spo = str(st.get("spo", "—"))
            t = st.get("time")
            tok = st.get("tokps")
            time_str = f"{t:.1f}s" if isinstance(t, (int, float)) else "—"
            tokps_str = f"{tok:.0f}" if isinstance(tok, (int, float)) else "—"
            calls_str = str(st.get("calls", "—"))
            retry_str = str(st.get("retries", "—"))
            err_str = str(st.get("errors", "—"))
        elif st.get("status") == "running":
            # Show elapsed time while running
            start = st.get("start_time")
            elapsed = time.monotonic() - start if start else 0.0
            mentions = "…"
            spo = "…"
            time_str = f"{elapsed:.1f}s"
            tokps_str = "…"
            calls_str = "…"
            retry_str = "…"
            err_str = "…"
        else:
            mentions = spo = time_str = tokps_str = calls_str = retry_str = err_str = "—"

        table.add_row(
            str(idx),
            name,
            f"{tier_col}{tier}[/]",
            status_col,
            mentions,
            spo,
            time_str,
            tokps_str,
            calls_str,
            retry_str,
            err_str,
        )

    return table


def _render_log(log_buffer: collections.deque, visible_lines: int | None = None) -> Panel:
    """Build the auto-tailing log panel from current deque contents.

    Renders only the most-recent ``visible_lines`` (computed from terminal
    height by the caller) so the panel always shows the newest output —
    older lines remain in the buffer and stay in the user's terminal
    scrollback. ``BENCH_TUI_LOG_BUFFER`` raises the deque cap (default
    1000) for runs that produce more output.
    """
    all_lines = list(log_buffer)
    lines = all_lines[-visible_lines:] if visible_lines is not None and visible_lines > 0 else all_lines
    # Colour-code lines by severity heuristic
    rendered: list[Text] = []
    for line in lines:
        t = Text(line, no_wrap=True, overflow="fold")
        low = line.lower()
        if any(kw in low for kw in ("error", "fail", "exception", "traceback")):
            t.stylize("bold red")
        elif any(kw in low for kw in ("warn", "warning")):
            t.stylize("yellow")
        elif any(kw in low for kw in ("phase a complete", "phase b", "completed", "ok")):
            t.stylize("bold green")
        elif any(kw in low for kw in ("phase a:", "building", "running")):
            t.stylize("cyan")
        else:
            t.stylize("dim white")
        rendered.append(t)

    content = Text("\n").join(rendered) if rendered else Text("(waiting for output…)", style="dim white")
    subtitle = (
        f"[dim white]{len(lines)} of {len(all_lines)}/{_LOG_MAXLEN} lines · "
        f"newest at bottom · scroll terminal to see older[/]"
    )
    return Panel(
        content,
        title="[bold magenta]LOG[/]",
        border_style="dim magenta",
        subtitle=subtitle,
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Logging handler
# ---------------------------------------------------------------------------


class DequeHandler(logging.Handler):
    """Logging handler that appends formatted records to a deque.

    Also forwards each record to a fallback handler (default: stderr
    StreamHandler) so the file artifact is unchanged when running non-TTY.
    """

    def __init__(self, deque_: collections.deque, fallback: logging.Handler | None = None):
        super().__init__()
        self._deque = deque_
        self._fallback = fallback or logging.StreamHandler(sys.stderr)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            ts = time.strftime("%H:%M:%S", time.localtime(record.created))
            self._deque.append(f"{ts}  {msg}")
            self._fallback.emit(record)
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# BenchLiveUI — the main context manager
# ---------------------------------------------------------------------------


class BenchLiveUI:
    """Two-pane Rich Live context manager for the benchmark run loop.

    Usage::

        ui = BenchLiveUI(run_id=run.run_id, pipeline="exgraph",
                         models=models, sample_cap="5/domain")
        with ui:
            ui.log("Phase A: building cluster cache…")
            ui.set_status("gliner-medium", "running")
            # … do work …
            ui.set_status("gliner-medium", "ok", fixture=fixture)
            ui.log("Phase A complete")

    When ``force_plain=True`` or when stderr is not a TTY, the Live wrapper
    is skipped and all output falls through to plain stderr/stdout prints
    so CI and Tilt pipe output stays clean.
    """

    def __init__(
        self,
        *,
        run_id: str,
        pipeline: str,
        models: list[Any],
        sample_cap: str = "—",
        force_plain: bool = False,
        refresh_per_second: int = 4,
    ):
        self._run_id = run_id
        self._pipeline = pipeline
        self._models = models
        self._sample_cap = sample_cap
        self._refresh_per_second = refresh_per_second

        # Decide TTY mode once at construction time.
        self._tty_mode: bool = _RICH_AVAILABLE and not force_plain and sys.stderr.isatty()

        # State accumulator: ordered dict preserving model insertion order.
        self._model_states: dict[str, dict] = {}
        for m in models:
            tier = self._compute_tier(m.tags)
            self._model_states[m.name] = {
                "status": "queued",
                "tier": tier,
                "mentions": None,
                "spo": None,
                "time": None,
                "tokps": None,
                "calls": None,
                "retries": None,
                "errors": None,
                "start_time": None,
            }

        # Scrolling log buffer.
        self._log_buffer: collections.deque = collections.deque(maxlen=_LOG_MAXLEN)

        # Rich objects — only created in TTY mode.
        self._console: Console | None = None
        self._layout: Layout | None = None
        self._live: Live | None = None

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_tier(tags: list[str]) -> str:
        if "encoder" in tags:
            return "ENC"
        if "extraction-specialist" in tags:
            return "SPEC"
        if "cloud" in tags:
            return "CLOUD"
        if "tier1" in tags:
            return "T1"
        if "tier2" in tags:
            return "T2"
        return "LLM"

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> BenchLiveUI:
        if self._tty_mode:
            self._console = Console(stderr=True)
            self._layout = Layout()
            # Header is fixed (4 rows). Table grows with model count. Log gets
            # ratio=1 so it claims the rest of the terminal — taller terminal
            # = more visible log lines, no recompile needed. _render_log
            # auto-tails to the visible height.
            self._layout.split_column(
                Layout(name="header", size=4),
                Layout(name="table", size=max(8, len(self._models) + 4)),
                Layout(name="log", ratio=1, minimum_size=10),
            )
            self._live = Live(
                self._layout,
                console=self._console,
                refresh_per_second=self._refresh_per_second,
                screen=False,
                transient=False,
            )
            self._live.__enter__()
            # Install our DequeHandler on the root logger so library logs
            # land in the same buffer ui.log writes to. Removed in __exit__.
            self._log_handler = DequeHandler(self._log_buffer)
            self._log_handler.setFormatter(logging.Formatter("%(name)s  %(message)s"))
            logging.getLogger().addHandler(self._log_handler)
            self._update_layout()
        return self

    def __exit__(self, *args) -> None:
        if self._live is not None:
            # Final render before exiting so last state is visible.
            self._update_layout()
            self._live.__exit__(*args)
        if getattr(self, "_log_handler", None) is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, message: str) -> None:
        """Append a line to the log buffer.

        Thin compat shim — prefer ``logger.info(message)`` from anywhere in
        the codebase: the TUI installs a DequeHandler on the root logger
        that captures library logs into the same buffer this method writes
        to. The dual API exists only for legacy call sites.
        """
        # Route through python logging so call sites that already use logger
        # AND those that call ui.log() converge on the same handler chain.
        # Bypasses the formatter prefix so the output looks identical to the
        # historical ui.log() format ("HH:MM:SS  message").
        ts = time.strftime("%H:%M:%S")
        line = f"{ts}  {message}"
        self._log_buffer.append(line)
        if not self._tty_mode:
            print(line, flush=True)
        else:
            self._update_layout()

    def set_status(
        self,
        model_name: str,
        status: str,
        *,
        fixture: dict | None = None,
    ) -> None:
        """Update a model row.  ``fixture`` is the extraction result dict."""
        if model_name not in self._model_states:
            # Unknown model — add dynamically.
            self._model_states[model_name] = {"status": status, "tier": "LLM"}

        st = self._model_states[model_name]
        st["status"] = status

        if status == "running":
            st["start_time"] = time.monotonic()

        if fixture is not None:
            stats = fixture.get("stats") or {}
            st["mentions"] = stats.get("mention_count", 0)
            st["spo"] = stats.get("assertion_count", 0)
            st["time"] = stats.get("duration_s", 0.0)
            st["tokps"] = stats.get("tokens_per_sec", 0.0)
            st["calls"] = stats.get("llm_call_count", 0) or 0
            st["retries"] = (stats.get("mention_retries") or 0) + (stats.get("proposition_retries") or 0)
            st["errors"] = stats.get("errors", 0) or 0

        if not self._tty_mode:
            # Plain-mode: emit a row to stdout (same format as the old _row()).
            self._plain_row(model_name, st)
        else:
            self._update_layout()

    def _plain_row(self, name: str, st: dict) -> None:
        """Emit a plain-text row to stdout (non-TTY path)."""
        idx = list(self._model_states.keys()).index(name) + 1
        tier = st.get("tier", "LLM")
        status = st.get("status", "?")
        if st.get("mentions") is not None:
            mentions = str(st["mentions"])
            spo = str(st.get("spo", "—"))
            t = st.get("time")
            tok = st.get("tokps")
            time_str = f"{t:.1f}s" if isinstance(t, (int, float)) else "—"
            tokps_str = f"{tok:.0f}" if isinstance(tok, (int, float)) else "—"
            calls = str(st.get("calls", "—"))
            retries = str(st.get("retries", "—"))
            errors = str(st.get("errors", "—"))
        else:
            mentions = spo = time_str = tokps_str = calls = retries = errors = "—"

        print(
            f"  {idx:>2} {name:<22} {tier:<8} {status:<10} "
            f"{mentions:>9} {spo:>5} {time_str:>9} {tokps_str:>7} "
            f"{calls:>6} {retries:>5} {errors:>4}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Internal layout refresh
    # ------------------------------------------------------------------

    def _update_layout(self) -> None:
        if self._layout is None or self._live is None:
            return
        self._layout["header"].update(_render_header(self._run_id, self._pipeline, self._models, self._sample_cap))
        self._layout["table"].update(
            Panel(
                _render_table(self._model_states),
                title="[bold magenta]MODELS[/]",
                border_style="dim magenta",
                padding=(0, 0),
            )
        )
        # Compute visible-line budget for the log pane: console height minus
        # header (4) and table (table_size + Panel chrome). Leaves the LOG
        # panel auto-tailing to whatever fits — terminal resize "just works".
        log_visible = self._compute_log_visible()
        self._layout["log"].update(_render_log(self._log_buffer, visible_lines=log_visible))

    def _compute_log_visible(self) -> int:
        """How many log lines fit in the LOG panel right now."""
        if self._console is None:
            return 30
        header_h = 4
        table_h = max(8, len(self._models) + 4)
        # 2 for the panel border + ~1 for subtitle wrap = 3 chrome rows
        return max(5, self._console.height - header_h - table_h - 3)
