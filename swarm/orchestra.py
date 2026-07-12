"""ThreadPool orchestra: deep GMGN enrich + hard short-circuit + specialists."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from fetchers.gmgn import enrich_for_swarm
from swarm.agents import run_orchestrator, run_specialist
from swarm.models import OrchestratorVerdict, SpecialistReport

SPECIALIST_NAMES = ("security", "holders", "traders", "liquidity", "social")


def _hard_rug(token: dict) -> OrchestratorVerdict | None:
    """Auto-rug obvious scams without spending LLM calls."""
    reasons: list[str] = []
    rug = token.get("rug_ratio")
    top = token.get("top_holders_pct")
    dev = token.get("dev_holdings_pct")
    flags = token.get("investigation_flags") or []
    dist = token.get("holder_distribution") or {}

    if token.get("phishing_check") == "fail":
        reasons.append(
            "phishing_check=fail "
            + ",".join(token.get("phishing_flags") or [])
        )
    if "honeypot" in flags or token.get("is_honeypot") in (True, "yes", "true", 1):
        reasons.append("honeypot")
    if token.get("is_blacklist") or "blacklist" in str(flags):
        reasons.append("blacklist")
    if "wash_trading" in flags:
        reasons.append("wash_trading detected")
    if "no_gmgn_data" in flags:
        reasons.append("no GMGN token info/security data — treat as unknown/fake")

    analysis = token.get("analysis_score_pct")
    try:
        if analysis is not None and float(analysis) < 25:
            reasons.append(f"analysis_score={analysis}% < 25")
    except (TypeError, ValueError):
        pass

    try:
        if rug is not None and float(rug) > 0.5:
            reasons.append(f"rug_ratio={rug} > 0.5")
    except (TypeError, ValueError):
        pass
    try:
        if top is not None and float(top) > 80:
            reasons.append(f"top_holders_pct={top} > 80")
    except (TypeError, ValueError):
        pass
    try:
        if dist.get("top1_pct") is not None and float(dist["top1_pct"]) > 70:
            reasons.append(f"top1_holder={dist['top1_pct']}% > 70")
    except (TypeError, ValueError):
        pass
    try:
        if dev is not None and float(dev) > 30:
            reasons.append(f"dev_holdings_pct={dev} > 30")
    except (TypeError, ValueError):
        pass

    if not reasons:
        return None
    return OrchestratorVerdict(
        verdict="rug",
        confidence=95,
        reasons=reasons,
        reports=[],
        short_circuited=True,
    )


def analyze_token(token: dict) -> OrchestratorVerdict:
    """Enrich with GMGN deep analytics (mutates token), then run the swarm."""
    enriched = enrich_for_swarm(token)
    token.update(enriched)

    hard = _hard_rug(token)
    if hard is not None:
        return hard

    reports: list[SpecialistReport] = []
    errors: list[str] = []
    workers = max(1, min(config.MONITOR_SWARM_WORKERS, len(SPECIALIST_NAMES)))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_specialist, name, token): name
            for name in SPECIALIST_NAMES
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                reports.append(fut.result())
            except Exception as exc:
                errors.append(f"{name} failed: {exc}")
                reports.append(SpecialistReport(
                    agent=name, score=40, flags=["agent_error"],
                    summary=str(exc)[:200],
                ))

    if len(reports) < 2:
        return OrchestratorVerdict(
            verdict="suspicious",
            confidence=30,
            reasons=errors or ["too few specialist reports"],
            reports=reports,
            short_circuited=False,
        )

    try:
        return run_orchestrator(token, reports)
    except Exception as exc:
        avg = sum(r.score for r in reports) / len(reports)
        if avg >= 75:
            label = "legit"
        elif avg <= 40:
            label = "rug"
        else:
            label = "suspicious"
        return OrchestratorVerdict(
            verdict=label,  # type: ignore[arg-type]
            confidence=int(avg),
            reasons=[f"orchestrator failed: {exc}", f"avg_specialist_score={avg:.0f}"] + errors,
            reports=reports,
            short_circuited=False,
        )
