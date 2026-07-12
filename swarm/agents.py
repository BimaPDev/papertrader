"""Specialist + orchestrator prompts for the rug/legit swarm."""

from __future__ import annotations

import json

from llm import complete_json
from swarm.models import OrchestratorVerdict, SpecialistReport

SPECIALISTS = {
    "security": """You are the SecurityAgent in a memecoin due-diligence swarm.
Score contract / scam risk using: rug_ratio, honeypot, blacklist, phishing_check,
is_show_alert, renounced mint/freeze, burn/lock, wash/bundler/insider rates,
analysis_score_pct. High score = safer. phishing_check=fail or honeypot → very low score.
Use only the dossier JSON. Be concise. Return JSON matching the schema.""",
    "holders": """You are the HolderAgent in a memecoin due-diligence swarm.
Score holder distribution using: holder_distribution (top1_pct, top10_pct,
token_holders, suspicious_holders), wallet_tags (smart/sniper/bundler/fresh/rat),
bot_degen_rate, fresh_wallet_rate, top_holders_sample, dev holdings.
High concentration or many suspicious holders → low score. High score = safer.
Use only the dossier JSON. Return JSON matching the schema.""",
    "traders": """You are the TradersAgent in a memecoin due-diligence swarm.
Score trading quality using: creator_7d (realized_pnl, win_rate, buys/sells),
traders_7d_proxy (realized_pnl_sum, win_rate, winners/losers), avg_buy_mc /
avg_buy_price (Avg Buy MC Distribution), analysis_score_pct.
Creator with toxic 7d win rate / dump pattern → low score.
Healthy win rate and organic avg-buy MC vs current mcap → higher score.
High score = safer. Use only the dossier JSON. Return JSON matching the schema.""",
    "liquidity": """You are the LiquidityAgent in a memecoin due-diligence swarm.
Score liquidity/volume/age/stage risk. New bonding-curve tokens are inherently riskier
than Migrated tokens with real DEX liquidity. Consider lock_percent / burn_status.
High score = safer. Use only the dossier JSON. Return JSON matching the schema.""",
    "social": """You are the SocialAgent in a memecoin due-diligence swarm.
Score social footprint (twitter/telegram/website). Missing socials = caution, not auto-rug.
Obvious spam or copycat links = red flag. High score = safer.
Use only the dossier JSON. Return JSON matching the schema.""",
}

ORCHESTRATOR_SYSTEM = """You are the OrchestratorAgent for a memecoin rug/legit swarm.
You receive the raw token dossier plus specialist reports (security, holders,
traders, liquidity, social). Each specialist score is 0-100 where higher = safer.

Key dossier fields to weigh heavily:
- phishing_check (pass/fail), analysis_score_pct (0-100 Analysis)
- creator_7d realized_pnl + win_rate, traders_7d_proxy win_rate
- holder_distribution / Avg Buy MC (avg_buy_mc)
- honeypot / blacklist / extreme top1 concentration

Decide a final verdict:
- rug: clear scam / phishing fail / honeypot / extreme concentration
- suspicious: mixed or incomplete evidence; do not paper-buy
- legit: specialists broadly agree the token clears a cautious bar

Be conservative for paper trading: when unsure, prefer suspicious over legit.
Return JSON matching the schema. Include short reasons. Your `reports` field may
be empty — the caller will attach the specialist reports.
"""

_DOSSIER_KEYS = [
    "mint", "symbol", "name", "kind", "market_cap", "liquidity_usd", "volume",
    "volume_h1", "price", "holders", "sniper_count", "sniper_pct",
    "top_holders_pct", "dev_holdings_pct", "age_minutes", "rug_ratio",
    "smart_degen_count", "launchpad", "exchange", "twitter", "telegram",
    "website", "source", "gmgn_url", "pumpfun_url", "dex_url",
    "side", "wallet", "trade_usd", "tx_hash",
    "renounced_mint", "renounced_freeze", "is_honeypot", "is_wash_trading",
    "burn_status", "creator_token_status", "bundler_rate", "insider_rate",
    "investigation_flags", "investigation_quick",
    # Deep GMGN analytics
    "analysis_score_pct", "phishing_check", "phishing_flags",
    "is_show_alert", "is_blacklist", "security_flags", "lock_percent",
    "holder_distribution", "wallet_tags", "bot_degen_rate", "fresh_wallet_rate",
    "avg_buy_mc", "avg_buy_price", "avg_buy_mc_sample_n",
    "traders_7d_proxy", "creator_7d", "creator_address",
    "top_holders_sample", "stat_snapshot",
]


def _dossier_blob(token: dict) -> str:
    slim = {k: token.get(k) for k in _DOSSIER_KEYS if token.get(k) is not None}
    slim["stage"] = token.get("kind")
    return json.dumps(slim, default=str)


def run_specialist(name: str, token: dict) -> SpecialistReport:
    system = SPECIALISTS[name]
    user = f"Agent name to set in `agent` field: {name}\n\nDossier:\n{_dossier_blob(token)}"
    report = complete_json(system, user, SpecialistReport)
    return report.model_copy(update={"agent": name})


def run_orchestrator(token: dict, reports: list[SpecialistReport]) -> OrchestratorVerdict:
    payload = {
        "dossier": json.loads(_dossier_blob(token)),
        "specialists": [r.model_dump() for r in reports],
    }
    verdict = complete_json(
        ORCHESTRATOR_SYSTEM,
        json.dumps(payload, default=str),
        OrchestratorVerdict,
    )
    return verdict.model_copy(update={"reports": reports, "short_circuited": False})
