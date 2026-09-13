"""track -> ANALYSIS -> log -> improve.

The gap this closes: the old loop went straight from one post's raw
numbers to a single local model's "improve" suggestion — no comparison
against history, no check on whether the *previous* suggestion actually
correlated with anything moving. That's not analysis, it's one model
guessing from N=1.

This step queries warehouse/local.duckdb's `performance_history` (built by
`dbt run` in warehouse/ — see warehouse/README.md) for this
product/channel's real timeline, computes a trend and whether the last
improvement_note's suggestion looks like it helped, and — because sample
sizes here are still small enough that any of this could be noise —
prints the verdict for a human to confirm or override before downstream
steps (discuss.py) build on it. That's the human-in-the-loop half of this
step; discuss.py is the multi-model half.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import settings
from .human_loop import ReviewInterrupted

TREND_THRESHOLD = 0.05  # +/-5% vs historical average before calling it a trend, not noise


@dataclass
class AnalysisResult:
    product: str
    channel: str
    n_prior_posts: int
    historical_avg_ctr: float | None
    current_ctr: float
    trend: str  # no_warehouse | no_history | insufficient_for_trend | improving | declining | flat
    prior_suggestion: str
    prior_suggestion_effectiveness: str  # unknown | looks_effective | looks_ineffective
    evidence: str
    human_confirmed: bool | None = None
    human_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "channel": self.channel,
            "n_prior_posts": self.n_prior_posts,
            "historical_avg_ctr": self.historical_avg_ctr,
            "current_ctr": self.current_ctr,
            "trend": self.trend,
            "prior_suggestion": self.prior_suggestion,
            "prior_suggestion_effectiveness": self.prior_suggestion_effectiveness,
            "evidence": self.evidence,
            "human_confirmed": self.human_confirmed,
            "human_note": self.human_note,
        }


def _query_history(product_name: str, channel: str) -> list[dict[str, Any]] | None:
    """Reads warehouse/local.duckdb's performance_history. Returns None if
    the warehouse hasn't been built yet, so the caller can fall back
    cleanly instead of crashing the pipeline on a missing file.
    """
    db_path = settings.project_root / "warehouse" / "local.duckdb"
    if not db_path.exists():
        return None
    try:
        import duckdb

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = con.execute(
                "select ts, ctr, improvement_note from performance_history "
                "where product = ? and channel = ? and status = 'published' order by ts",
                [product_name, channel],
            ).fetchall()
        finally:
            con.close()
        return [{"ts": r[0], "ctr": r[1], "improvement_note": r[2]} for r in rows]
    except Exception:  # noqa: BLE001 — analysis degrading gracefully beats crashing the pipeline
        return None


def analyze_performance(
    product_name: str,
    channel: str,
    current_ctr: float,
    interactive: bool = True,
) -> AnalysisResult:
    """Cross-validates this run's result against this product/channel's
    real history in the warehouse — not a single model guessing from one
    post's raw numbers. `interactive=True` (the default, matching every
    other human-in-the-loop gate in this pipeline) prints the computed
    verdict and lets a human confirm or override it before discuss.py
    trusts it.
    """
    history = _query_history(product_name, channel)

    if history is None:
        result = AnalysisResult(
            product=product_name, channel=channel, n_prior_posts=0,
            historical_avg_ctr=None, current_ctr=current_ctr, trend="no_warehouse",
            prior_suggestion="", prior_suggestion_effectiveness="unknown",
            evidence="warehouse/local.duckdb not found — run `dbt run` in warehouse/ to enable "
                     "real cross-validation. Proceeding with no history.",
        )
    elif not history:
        result = AnalysisResult(
            product=product_name, channel=channel, n_prior_posts=0,
            historical_avg_ctr=None, current_ctr=current_ctr, trend="no_history",
            prior_suggestion="", prior_suggestion_effectiveness="unknown",
            evidence="No prior published posts for this product/channel — this is the first "
                     "data point, nothing to cross-validate against yet.",
        )
    else:
        n = len(history)
        avg_ctr = sum(h["ctr"] or 0.0 for h in history) / n
        delta = current_ctr - avg_ctr

        if n < 2:
            trend = "insufficient_for_trend"
        elif avg_ctr == 0:
            trend = "improving" if current_ctr > 0 else "flat"
        elif delta > avg_ctr * TREND_THRESHOLD:
            trend = "improving"
        elif delta < -avg_ctr * TREND_THRESHOLD:
            trend = "declining"
        else:
            trend = "flat"

        last = history[-1]
        prior_suggestion = last.get("improvement_note") or ""
        last_ctr = last["ctr"] or 0.0
        if not prior_suggestion:
            effectiveness = "unknown"
        elif current_ctr > last_ctr:
            effectiveness = "looks_effective"
        else:
            effectiveness = "looks_ineffective"

        evidence = (
            f"{n} prior published post(s); historical avg ctr={avg_ctr:.4f}, "
            f"current ctr={current_ctr:.4f} (delta={delta:+.4f})."
        )
        if prior_suggestion:
            support = "supports" if effectiveness == "looks_effective" else "does not support"
            evidence += (
                f" Prior suggestion was {prior_suggestion!r} — ctr went from {last_ctr:.4f} to "
                f"{current_ctr:.4f} since, which {support} it (single data point, not "
                f"statistically validated)."
            )

        result = AnalysisResult(
            product=product_name, channel=channel, n_prior_posts=n,
            historical_avg_ctr=avg_ctr, current_ctr=current_ctr, trend=trend,
            prior_suggestion=prior_suggestion, prior_suggestion_effectiveness=effectiveness,
            evidence=evidence,
        )

    if interactive:
        result = _confirm_with_human(result)
    return result


def _confirm_with_human(result: AnalysisResult) -> AnalysisResult:
    print("\n" + "=" * 60)
    print(f"ANALYSIS  {result.product} · {result.channel}")
    print("-" * 60)
    print(f"prior published posts : {result.n_prior_posts}")
    if result.historical_avg_ctr is not None:
        print(f"historical avg ctr     : {result.historical_avg_ctr:.4f}")
    print(f"current ctr            : {result.current_ctr:.4f}")
    print(f"trend                  : {result.trend}")
    if result.prior_suggestion:
        print(f"prior suggestion       : {result.prior_suggestion!r}")
        print(f"looks                  : {result.prior_suggestion_effectiveness}")
    print(result.evidence)
    print("=" * 60)
    try:
        choice = input("Does this analysis look right? [Y]es / [n]o, override > ").strip().lower()
    except (EOFError, KeyboardInterrupt) as e:
        raise ReviewInterrupted("Analysis review interrupted (stdin closed or Ctrl+C)") from e
    if choice.startswith("n"):
        note = input("What should we conclude instead? (stored alongside the computed verdict): ").strip()
        result.human_confirmed = False
        result.human_note = note
    else:
        result.human_confirmed = True
    return result
