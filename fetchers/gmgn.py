"""GMGN.ai Solana via official OpenAPI (https://openapi.gmgn.ai).

Trenches (New / Almost / Migrated), copy-trade wallet activity, and token
info/security for rug/legit investigation.

Requires GMGN_API_KEY (create at https://gmgn.ai/ai). Website scrapes are
Cloudflare-blocked; this uses the documented read API with X-APIKEY +
timestamp + client_id auth.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

import config

load_dotenv()

HOST = "https://openapi.gmgn.ai"
TRENCHES_PATH = "/v1/trenches"
WALLET_ACTIVITY_PATH = "/v1/user/wallet_activity"
TOKEN_INFO_PATH = "/v1/token/info"
TOKEN_SECURITY_PATH = "/v1/token/security"
TOKEN_HOLDERS_PATH = "/v1/market/token_top_holders"
TOKEN_TRADERS_PATH = "/v1/market/token_top_traders"
WALLET_STATS_PATH = "/v1/user/wallet_stats"
SMARTMONEY_PATH = "/v1/user/smartmoney"
KOL_PATH = "/v1/user/kol"

# Default Sol launchpads (same allow-list as gmgn-cli)
SOL_PLATFORMS = [
    "Pump.fun", "pump_mayhem", "pump_mayhem_agent", "pump_agent",
    "letsbonk", "bonkers", "bags", "memoo", "liquid", "bankr", "zora",
    "surge", "anoncoin", "moonshot_app", "wendotdev", "heaven", "sugar",
    "token_mill", "believe", "trendsfun", "trends_fun", "jup_studio",
    "Moonshot", "boop", "ray_launchpad", "meteora_virtual_curve", "xstocks",
]
SOL_QUOTE_ADDRESS_TYPES = [4, 5, 3, 1, 13, 0]

# Request type → response key (near_completion is returned as "pump")
RESPONSE_KEY = {
    "new_creation": "new_creation",
    "near_completion": "pump",
    "completed": "completed",
}

KIND_FOR_TYPE = {
    "new_creation": "new",          # GMGN "New"
    "near_completion": "almost",    # GMGN "Almost"
    "completed": "migrated",        # GMGN "Migrated"
}

KIND_LABEL = {
    "new": "New",
    "almost": "Almost",
    "migrated": "Migrated",
    "copy": "Copy",
    "dex_profile": "DexProfile",
    "dex_boost": "DexBoost",
    "dex": "DexScreener",
}


class GmgnConfigError(RuntimeError):
    pass


def _api_key() -> str:
    return (os.getenv("GMGN_API_KEY") or "").strip()


def available() -> bool:
    return bool(_api_key())


def _require_key() -> str:
    key = _api_key()
    if not key:
        raise GmgnConfigError(
            "GMGN_API_KEY not set — get one at https://gmgn.ai/ai and add it to .env"
        )
    return key


def _headers(key: str) -> dict:
    return {
        "X-APIKEY": key,
        "Content-Type": "application/json",
        "User-Agent": "PaperTraderGraduationMonitor/1.0",
        "Accept": "application/json",
    }


def _auth_params(extra: dict | None = None) -> dict:
    params = {
        "timestamp": int(datetime.now(timezone.utc).timestamp()),
        "client_id": str(uuid.uuid4()),
    }
    if extra:
        params.update(extra)
    return params


def _unwrap(payload):
    """Raise on GMGN error codes; return data payload."""
    if isinstance(payload, dict) and payload.get("code") not in (0, None):
        raise RuntimeError(
            f"GMGN API error code={payload.get('code')} "
            f"error={payload.get('error')} message={payload.get('message')}"
        )
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _get(path: str, params: dict, timeout: int = 25):
    key = _require_key()
    url = f"{HOST}{path}"
    full = _auth_params(params)
    resp = requests.get(url, headers=_headers(key), params=full, timeout=timeout)
    resp.raise_for_status()
    return _unwrap(resp.json())


def _post(path: str, params: dict, body: dict, timeout: int = 25):
    key = _require_key()
    url = f"{HOST}{path}"
    full = _auth_params(params)
    resp = requests.post(
        url, headers=_headers(key), params=full, json=body, timeout=timeout
    )
    resp.raise_for_status()
    return _unwrap(resp.json())


def _age_minutes_from_unix(ts) -> float | None:
    if ts is None:
        return None
    try:
        ts = float(ts)
        # GMGN uses seconds; tolerate ms
        if ts > 1e12:
            ts /= 1000.0
        then = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 60.0


def _iso_from_unix(ts) -> str | None:
    if ts is None:
        return None
    try:
        ts = float(ts)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _pct(x) -> float | None:
    """Normalize 0–1 ratios or already-percent values to percent."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if x <= 1.0:
        return x * 100.0
    return x


def _build_body(types: list[str], limit: int) -> dict:
    section = {
        "filters": ["offchain", "onchain"],
        "launchpad_platform_v2": True,
        "limit": limit,
        "launchpad_platform": SOL_PLATFORMS,
        "quote_address_type": SOL_QUOTE_ADDRESS_TYPES,
    }
    if config.MONITOR_GMGN_FILTER_PRESET == "safe":
        section.update(max_rug_ratio=0.3, max_bundler_rate=0.3, max_insider_ratio=0.3)
    elif config.MONITOR_GMGN_FILTER_PRESET == "strict":
        section.update(
            max_rug_ratio=0.3,
            max_bundler_rate=0.3,
            max_insider_ratio=0.3,
            min_smart_degen_count=1,
            min_volume_24h=1000,
        )
    body: dict = {"version": "v2"}
    for t in types:
        body[t] = dict(section)
    return body


def _normalize(item: dict, req_type: str) -> dict | None:
    mint = item.get("address") or ""
    if not mint or mint in config.EXCLUDED_TOKENS:
        return None

    kind = KIND_FOR_TYPE[req_type]
    # Prefer completion / open time for grads; creation time otherwise
    event_ts = (
        item.get("complete_timestamp")
        or item.get("open_timestamp")
        or item.get("created_timestamp")
    )
    age = _age_minutes_from_unix(event_ts)
    mcap = float(item.get("usd_market_cap") or item.get("market_cap") or 0)
    holders = int(item.get("holder_count") or 0)
    top_pct = _pct(item.get("top_10_holder_rate"))
    dev_pct = _pct(item.get("dev_team_hold_rate") or item.get("creator_balance_rate"))
    liq = item.get("liquidity")
    try:
        liq = float(liq) if liq is not None else None
    except (TypeError, ValueError):
        liq = None
    volume = item.get("volume_1h") or item.get("volume_24h")
    try:
        volume = float(volume) if volume is not None else None
    except (TypeError, ValueError):
        volume = None

    if kind == "migrated":
        if age is not None and age > config.MONITOR_MAX_AGE_MINUTES:
            return None
        if mcap < config.MONITOR_MIN_MARKET_CAP_USD:
            return None
        if holders and holders < config.MONITOR_MIN_HOLDERS:
            return None
        if top_pct is not None and top_pct > config.MONITOR_MAX_TOP_HOLDERS_PCT:
            return None
        if dev_pct is not None and dev_pct > config.MONITOR_MAX_DEV_HOLDINGS_PCT:
            return None
        if liq is not None and liq < config.MONITOR_MIN_LIQUIDITY_USD:
            return None
    elif kind == "almost":
        # GMGN already defines "Almost"; don't re-apply pump.fun mcap band
        if age is not None and age > config.MONITOR_MAX_AGE_MINUTES:
            return None
    elif kind == "new":
        if age is not None and age > config.MONITOR_NEW_MAX_AGE_MINUTES:
            return None
        # Prefer created_timestamp for New-tab age
        created_age = _age_minutes_from_unix(item.get("created_timestamp"))
        if created_age is not None and created_age > config.MONITOR_NEW_MAX_AGE_MINUTES:
            return None
        age = created_age if created_age is not None else age

    rug = item.get("rug_ratio")
    try:
        rug = float(rug) if rug is not None else None
    except (TypeError, ValueError):
        rug = None
    if rug is not None and rug > config.MONITOR_GMGN_MAX_RUG_RATIO:
        return None

    return {
        "mint": mint,
        "symbol": item.get("symbol") or mint[:6],
        "name": item.get("name") or "",
        "kind": kind,
        "market_cap": mcap,
        "volume": volume,
        "price": float(item["price"]) if item.get("price") not in (None, "") else None,
        "holders": holders or None,
        "sniper_count": int(item["sniper_count"]) if item.get("sniper_count") is not None else None,
        "sniper_pct": None,
        "top_holders_pct": top_pct,
        "dev_holdings_pct": dev_pct,
        "graduation_at": _iso_from_unix(item.get("complete_timestamp") or item.get("open_timestamp")),
        "created_at": _iso_from_unix(item.get("created_timestamp")),
        "age_minutes": round(age, 2) if age is not None else None,
        "pool_address": item.get("pool_address") or item.get("pair_address"),
        "liquidity_usd": liq,
        "twitter": item.get("twitter") or None,
        "telegram": item.get("telegram") or None,
        "website": item.get("website") or None,
        "image_url": item.get("logo") or item.get("image_url") or None,
        "rug_ratio": rug,
        "smart_degen_count": item.get("smart_degen_count"),
        "launchpad": item.get("launchpad_platform"),
        "exchange": item.get("exchange"),
        "source": f"gmgn_{req_type}",
        "gmgn_url": f"https://gmgn.ai/sol/token/{mint}",
        "pumpfun_url": f"https://pump.fun/coin/{mint}",
        "dex_url": f"https://gmgn.ai/sol/token/{mint}",
    }


def fetch_trenches(
    types: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Fetch GMGN trenches for Solana and normalize to monitor token dicts.

    Raises GmgnConfigError if no API key. On HTTP/API failure raises normally
    so the monitor can log and keep other sources running.
    """
    types = types or list(config.MONITOR_GMGN_TYPES)
    limit = limit or min(config.MONITOR_FETCH_LIMIT, 80)
    body = _build_body(types, limit)
    data = _post(TRENCHES_PATH, {"chain": "sol"}, body) or {}
    if not isinstance(data, dict):
        data = {}
    out: list[dict] = []
    for req_type in types:
        bucket = data.get(RESPONSE_KEY.get(req_type, req_type)) or []
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if not isinstance(item, dict):
                continue
            row = _normalize(item, req_type)
            if row:
                out.append(row)
    return out


def _activity_token(item: dict) -> tuple[str, str, str]:
    """Extract mint, symbol, name from a wallet_activity or track trade row."""
    token = item.get("token") or item.get("base_token") or {}
    if not isinstance(token, dict):
        token = {}
    mint = (
        item.get("base_address")
        or item.get("token_address")
        or token.get("address")
        or ""
    )
    symbol = token.get("symbol") or item.get("symbol") or (mint[:6] if mint else "?")
    name = token.get("name") or item.get("name") or ""
    return mint, symbol, name


def _normalize_activity(
    item: dict,
    *,
    wallet: str,
    source: str,
) -> dict | None:
    mint, symbol, name = _activity_token(item)
    if not mint or mint in config.EXCLUDED_TOKENS:
        return None

    side = (item.get("event_type") or item.get("type") or item.get("side") or "").lower()
    if side in ("buy", "sell"):
        pass
    elif side in ("transferin", "transfer_in", "add"):
        side = "buy"
    elif side in ("transferout", "transfer_out", "remove"):
        side = "sell"
    else:
        return None

    cost = item.get("cost_usd") or item.get("amount_usd") or item.get("buy_cost_usd")
    try:
        cost = float(cost) if cost not in (None, "") else None
    except (TypeError, ValueError):
        cost = None
    if cost is not None and cost < config.MONITOR_COPY_MIN_USD:
        return None

    ts = item.get("timestamp") or item.get("block_timestamp") or item.get("time")
    age = _age_minutes_from_unix(ts)
    if (
        age is not None
        and config.MONITOR_COPY_MAX_AGE_MINUTES
        and age > config.MONITOR_COPY_MAX_AGE_MINUTES
    ):
        return None

    tx = item.get("transaction_hash") or item.get("tx_hash") or item.get("hash") or ""
    maker = item.get("maker") or wallet
    price = item.get("price_usd") or item.get("price")
    try:
        price = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price = None

    return {
        "mint": mint,
        "symbol": symbol,
        "name": name,
        "kind": "copy",
        "side": side,
        "wallet": maker,
        "tx_hash": tx,
        "trade_usd": cost,
        "price": price,
        "price_usd": price,
        "trade_at": _iso_from_unix(ts),
        "age_minutes": round(age, 2) if age is not None else None,
        "source": source,
        "gmgn_url": f"https://gmgn.ai/sol/token/{mint}",
        "pumpfun_url": f"https://pump.fun/coin/{mint}",
        "dex_url": f"https://gmgn.ai/sol/token/{mint}",
        "wallet_url": f"https://gmgn.ai/sol/address/{maker}",
        "seen_key": f"copy:{maker}:{tx or mint}:{side}:{ts or ''}",
    }


def fetch_wallet_activity(
    wallet: str,
    *,
    limit: int | None = None,
    types: list[str] | None = None,
) -> list[dict]:
    """Recent buy/sell activity for one Solana wallet (copy-trade target)."""
    limit = limit or min(config.MONITOR_COPY_FETCH_LIMIT, 50)
    types = types or list(config.MONITOR_COPY_SIDES)
    params: dict = {
        "chain": "sol",
        "wallet_address": wallet,
        "limit": limit,
    }
    # GMGN accepts repeated type=; requests encodes list as type=a&type=b
    if types:
        params["type"] = types
    data = _get(WALLET_ACTIVITY_PATH, params) or {}
    activities = []
    if isinstance(data, dict):
        activities = data.get("activities") or data.get("list") or data.get("data") or []
    elif isinstance(data, list):
        activities = data
    out: list[dict] = []
    for item in activities:
        if not isinstance(item, dict):
            continue
        row = _normalize_activity(item, wallet=wallet, source=f"gmgn_copy:{wallet[:6]}")
        if row:
            out.append(row)
    return out


def fetch_track_trades(
    kind: str,
    *,
    limit: int | None = None,
    side: str | None = None,
) -> list[dict]:
    """Public Smart Money or KOL trade feed (no wallet list needed).

    kind: 'smartmoney' | 'kol'
    """
    path = SMARTMONEY_PATH if kind == "smartmoney" else KOL_PATH
    limit = limit or min(config.MONITOR_COPY_FETCH_LIMIT, 50)
    params: dict = {"chain": "sol", "limit": limit}
    data = _get(path, params) or {}
    rows = []
    if isinstance(data, dict):
        rows = data.get("list") or data.get("activities") or []
    elif isinstance(data, list):
        rows = data
    out: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        trade_side = (item.get("side") or "").lower()
        if side and trade_side != side:
            continue
        if trade_side and trade_side not in config.MONITOR_COPY_SIDES:
            continue
        maker = item.get("maker") or "unknown"
        row = _normalize_activity(
            item, wallet=maker, source=f"gmgn_{kind}"
        )
        if row:
            out.append(row)
    return out


def fetch_token_info(mint: str) -> dict:
    data = _get(TOKEN_INFO_PATH, {"chain": "sol", "address": mint})
    return data if isinstance(data, dict) else {}


def fetch_token_security(mint: str) -> dict:
    data = _get(TOKEN_SECURITY_PATH, {"chain": "sol", "address": mint})
    return data if isinstance(data, dict) else {}


def fetch_token_holders(mint: str, limit: int = 50, order_by: str = "amount_percentage") -> list[dict]:
    data = _get(TOKEN_HOLDERS_PATH, {
        "chain": "sol", "address": mint, "limit": limit, "order_by": order_by,
    })
    if isinstance(data, dict):
        return data.get("list") or []
    return data if isinstance(data, list) else []


def fetch_token_traders(mint: str, limit: int = 50, order_by: str = "profit") -> list[dict]:
    data = _get(TOKEN_TRADERS_PATH, {
        "chain": "sol", "address": mint, "limit": limit, "order_by": order_by,
    })
    if isinstance(data, dict):
        return data.get("list") or []
    return data if isinstance(data, list) else []


def fetch_wallet_stats(wallet: str, period: str = "7d") -> dict:
    data = _get(WALLET_STATS_PATH, {
        "chain": "sol", "wallet_address": wallet, "period": period,
    })
    return data if isinstance(data, dict) else {}


def _f(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct_amount(x) -> float | None:
    """Holder amount_percentage may be 0–1 or already 0–100."""
    v = _f(x)
    if v is None:
        return None
    return v * 100.0 if v <= 1.0 else v


def _compute_analysis_score(
    *,
    rug: float | None,
    top_pct: float | None,
    bot_rate: float | None,
    fresh_rate: float | None,
    bundler: float | None,
    insider: float | None,
    honeypot: bool,
    phishing: bool,
    suspicious_holders: int,
    holder_count: int | None,
    winrate: float | None,
    liq: float | None,
) -> float:
    """Synthetic 0–100 'Analysis' score similar to GMGN token page."""
    score = 70.0
    if honeypot or phishing:
        return 5.0
    if rug is not None:
        score -= min(40.0, rug * 80.0)
    if top_pct is not None:
        if top_pct > 50:
            score -= 25
        elif top_pct > 30:
            score -= 12
        elif top_pct < 15:
            score += 5
    if bot_rate is not None:
        score -= min(20.0, bot_rate * 40.0)
    if fresh_rate is not None and fresh_rate > 0.4:
        score -= 10
    if bundler is not None:
        score -= min(15.0, (bundler if bundler > 1 else bundler * 100) * 0.3)
    if insider is not None:
        score -= min(15.0, (insider if insider > 1 else insider * 100) * 0.3)
    if suspicious_holders >= 3:
        score -= 15
    elif suspicious_holders >= 1:
        score -= 5
    if holder_count is not None:
        if holder_count < 20:
            score -= 10
        elif holder_count > 200:
            score += 5
    if winrate is not None:
        if winrate >= 0.55:
            score += 8
        elif winrate < 0.35:
            score -= 8
    if liq is not None:
        if liq < 5_000:
            score -= 10
        elif liq > 50_000:
            score += 5
    return round(max(0.0, min(100.0, score)), 2)


def deep_analytics(mint: str) -> dict:
    """GMGN deep dive: security, holders, traders, creator 7d PnL/winrate, distribution."""
    errors: list[str] = []
    info: dict = {}
    security: dict = {}
    holders: list[dict] = []
    traders: list[dict] = []
    creator_stats: dict = {}

    try:
        info = fetch_token_info(mint)
    except Exception as exc:
        errors.append(f"token_info: {exc}")
    try:
        security = fetch_token_security(mint)
    except Exception as exc:
        errors.append(f"token_security: {exc}")
    try:
        holders = fetch_token_holders(mint)
    except Exception as exc:
        errors.append(f"token_holders: {exc}")
    try:
        traders = fetch_token_traders(mint)
    except Exception as exc:
        errors.append(f"token_traders: {exc}")

    stat = info.get("stat") if isinstance(info.get("stat"), dict) else {}
    dev = info.get("dev") if isinstance(info.get("dev"), dict) else {}
    tags = info.get("wallet_tags_stat") if isinstance(info.get("wallet_tags_stat"), dict) else {}
    price_obj = info.get("price") if isinstance(info.get("price"), dict) else {}
    price = _f(price_obj.get("price") if price_obj else None) or _f(info.get("price"))
    circ = _f(info.get("circulating_supply") or info.get("total_supply"))

    creator = dev.get("creator_address") or (info.get("dev") or {}).get("creator_address")
    if creator:
        try:
            creator_stats = fetch_wallet_stats(creator, period="7d")
        except Exception as exc:
            errors.append(f"wallet_stats: {exc}")

    # Holder distribution
    pcts = [_pct_amount(h.get("amount_percentage")) or 0.0 for h in holders]
    top1_pct = pcts[0] if pcts else None
    top10_pct = sum(pcts[:10]) if pcts else None
    suspicious_holders = sum(1 for h in holders if h.get("is_suspicious"))
    holder_count = info.get("holder_count") or stat.get("holder_count")
    try:
        holder_count = int(holder_count) if holder_count is not None else len(holders) or None
    except (TypeError, ValueError):
        holder_count = len(holders) or None

    # Trader PnL / win-rate proxy among top traders with realized data
    realized = [_f(t.get("realized_profit")) for t in traders]
    realized = [x for x in realized if x is not None]
    realized_pos = sum(1 for x in realized if x > 0)
    realized_neg = sum(1 for x in realized if x < 0)
    realized_sum = sum(realized) if realized else None
    traders_with_pnl = realized_pos + realized_neg
    traders_win_rate = (
        (realized_pos / traders_with_pnl) if traders_with_pnl > 0 else None
    )

    # Avg buy MC from trader avg_cost × supply
    costs = [_f(t.get("avg_cost")) for t in traders]
    costs = [c for c in costs if c and c > 0]
    avg_buy_price = (sum(costs) / len(costs)) if costs else None
    avg_buy_mc = (avg_buy_price * circ) if (avg_buy_price and circ) else None

    # Creator 7d
    pnl_stat = creator_stats.get("pnl_stat") if isinstance(creator_stats.get("pnl_stat"), dict) else {}
    creator_7d_realized = _f(creator_stats.get("realized_profit"))
    creator_7d_winrate = _f(pnl_stat.get("winrate"))
    creator_7d_buys = creator_stats.get("buy")
    creator_7d_sells = creator_stats.get("sell")

    rug = _f(security.get("rug_ratio") or info.get("rug_ratio") or stat.get("rug_ratio"))
    top_pct = _pct(
        security.get("top_10_holder_rate")
        or stat.get("top_10_holder_rate")
    )
    bot_rate = _f(stat.get("bot_degen_rate") or tags.get("bot_degen_rate"))
    if bot_rate is not None and bot_rate > 1:
        bot_rate = bot_rate / 100.0
    fresh_rate = _f(stat.get("fresh_wallet_rate"))
    if fresh_rate is not None and fresh_rate > 1:
        fresh_rate = fresh_rate / 100.0
    bundler = _f(
        security.get("bundler_trader_amount_rate")
        or stat.get("top_bundler_trader_percentage")
    )
    insider = _f(
        security.get("suspected_insider_hold_rate")
        or security.get("rat_trader_amount_rate")
        or stat.get("top_rat_trader_percentage")
    )
    liq = _f(info.get("liquidity"))

    is_honeypot = security.get("is_honeypot")
    honeypot = is_honeypot in (True, "yes", "true", 1) or security.get("honeypot") in (1, "1", True)
    blacklist = security.get("is_blacklist") in (True, "yes", "true", 1) or security.get("blacklist") in (1, "1", True)
    show_alert = bool(security.get("is_show_alert"))
    # Phishing / scam heuristics (GMGN UI "Phishing check")
    phishing = bool(
        show_alert
        or blacklist
        or honeypot
        or suspicious_holders >= 5
        or (security.get("flags") and "phishing" in str(security.get("flags")).lower())
    )
    phishing_flags: list[str] = []
    if show_alert:
        phishing_flags.append("gmgn_show_alert")
    if blacklist:
        phishing_flags.append("blacklist")
    if honeypot:
        phishing_flags.append("honeypot")
    if suspicious_holders:
        phishing_flags.append(f"suspicious_holders={suspicious_holders}")

    analysis_score = _compute_analysis_score(
        rug=rug,
        top_pct=top_pct,
        bot_rate=bot_rate,
        fresh_rate=fresh_rate,
        bundler=bundler,
        insider=insider,
        honeypot=honeypot,
        phishing=phishing,
        suspicious_holders=suspicious_holders,
        holder_count=holder_count,
        winrate=creator_7d_winrate if creator_7d_winrate is not None else traders_win_rate,
        liq=liq,
    )

    return {
        "deep_errors": errors,
        "analysis_score_pct": analysis_score,
        "phishing_check": "fail" if phishing else "pass",
        "phishing_flags": phishing_flags,
        "is_show_alert": show_alert,
        "is_blacklist": blacklist,
        "is_honeypot": is_honeypot,
        "security_flags": security.get("flags") or [],
        "burn_status": security.get("burn_status"),
        "renounced_mint": security.get("renounced_mint"),
        "renounced_freeze": security.get("renounced_freeze_account"),
        "lock_percent": _f((security.get("lock_summary") or {}).get("lock_percent"))
        if isinstance(security.get("lock_summary"), dict) else None,
        "holder_count": holder_count,
        "holder_distribution": {
            "token_holders": holder_count,
            "top1_pct": round(top1_pct, 2) if top1_pct is not None else None,
            "top10_pct": round(top10_pct, 2) if top10_pct is not None else None,
            "suspicious_holders": suspicious_holders,
            "listed": len(holders),
        },
        "wallet_tags": {
            "smart_wallets": tags.get("smart_wallets"),
            "fresh_wallets": tags.get("fresh_wallets"),
            "renowned_wallets": tags.get("renowned_wallets"),
            "sniper_wallets": tags.get("sniper_wallets"),
            "rat_trader_wallets": tags.get("rat_trader_wallets"),
            "bundler_wallets": tags.get("bundler_wallets"),
            "whale_wallets": tags.get("whale_wallets"),
        },
        "bot_degen_rate": bot_rate,
        "fresh_wallet_rate": fresh_rate,
        "avg_buy_mc": avg_buy_mc,
        "avg_buy_price": avg_buy_price,
        "avg_buy_mc_sample_n": len(costs),
        "traders_7d_proxy": {
            "realized_pnl_sum": realized_sum,
            "winners": realized_pos,
            "losers": realized_neg,
            "win_rate": traders_win_rate,
            "sample_n": len(realized),
        },
        "creator_address": creator,
        "creator_7d": {
            "realized_pnl": creator_7d_realized,
            "win_rate": creator_7d_winrate,
            "buys": creator_7d_buys,
            "sells": creator_7d_sells,
            "bought_cost": _f(creator_stats.get("bought_cost")),
            "tags": (creator_stats.get("common") or {}).get("tags")
            if isinstance(creator_stats.get("common"), dict) else None,
        },
        "top_holders_sample": [
            {
                "pct": _pct_amount(h.get("amount_percentage")),
                "realized_profit": _f(h.get("realized_profit")),
                "is_suspicious": bool(h.get("is_suspicious")),
                "tags": h.get("maker_token_tags") or h.get("tags"),
            }
            for h in holders[:10]
        ],
        "stat_snapshot": {
            "top70_sniper_hold_rate": _f(stat.get("top70_sniper_hold_rate")),
            "dev_team_hold_rate": _f(stat.get("dev_team_hold_rate")),
            "creator_created_count": stat.get("creator_created_count"),
            "private_vault_hold_rate": _f(stat.get("private_vault_hold_rate")),
        },
    }


def enrich_for_swarm(token: dict) -> dict:
    """Attach deep GMGN analytics onto a monitor/copy hit before the swarm runs."""
    mint = token.get("mint")
    if not mint or not available():
        return token
    try:
        deep = deep_analytics(mint)
    except Exception as exc:
        return {
            **token,
            "deep_errors": [str(exc)],
            "analysis_score_pct": None,
            "phishing_check": "unknown",
        }

    flags = list(token.get("investigation_flags") or [])
    if deep.get("phishing_check") == "fail":
        flags.append("phishing_fail")
        flags.extend(deep.get("phishing_flags") or [])
    if deep.get("analysis_score_pct") is not None and deep["analysis_score_pct"] < 35:
        flags.append(f"low_analysis={deep['analysis_score_pct']}")
    dist = deep.get("holder_distribution") or {}
    if dist.get("top1_pct") is not None and dist["top1_pct"] > 50:
        flags.append(f"top1={dist['top1_pct']:.0f}%")

    out = {**token, **{k: v for k, v in deep.items() if k != "deep_errors"}}
    out["investigation_flags"] = flags
    out["deep_errors"] = (token.get("deep_errors") or []) + (deep.get("deep_errors") or [])
    # Prefer security-derived concentration when present
    if deep.get("holder_distribution", {}).get("top10_pct") and out.get("top_holders_pct") is None:
        out["top_holders_pct"] = deep["holder_distribution"]["top10_pct"]
    if deep.get("holder_count") and not out.get("holders"):
        out["holders"] = deep["holder_count"]
    return out


def investigate_token(trade: dict) -> dict:
    """Merge wallet trade + GMGN token info/security into a swarm dossier.

    Marks the token with investigation fields (renounced, rug_ratio, wash,
    honeypot flags, etc.) so the existing rug/legit swarm can score it.
    """
    mint = trade["mint"]
    info: dict = {}
    security: dict = {}
    errors: list[str] = []
    try:
        info = fetch_token_info(mint)
    except Exception as exc:
        errors.append(f"token_info: {exc}")
    try:
        security = fetch_token_security(mint)
    except Exception as exc:
        errors.append(f"token_security: {exc}")

    price_obj = info.get("price") if isinstance(info.get("price"), dict) else {}
    price = _f(price_obj.get("price") if price_obj else None) or _f(info.get("price"))
    if price is None:
        price = trade.get("price") or trade.get("price_usd")

    circ = _f(info.get("circulating_supply") or info.get("total_supply"))
    mcap = (price * circ) if (price is not None and circ is not None) else None
    if mcap is None:
        mcap = _f(info.get("market_cap") or info.get("usd_market_cap"))

    liq = _f(info.get("liquidity"))
    pool = info.get("pool") if isinstance(info.get("pool"), dict) else {}
    if liq is None:
        liq = _f(pool.get("liquidity"))

    stat = info.get("stat") if isinstance(info.get("stat"), dict) else {}
    dev = info.get("dev") if isinstance(info.get("dev"), dict) else {}
    link = info.get("link") if isinstance(info.get("link"), dict) else {}
    tags = info.get("wallet_tags_stat") if isinstance(info.get("wallet_tags_stat"), dict) else {}

    top_pct = _pct(
        security.get("top_10_holder_rate")
        or stat.get("top_10_holder_rate")
        or dev.get("top_10_holder_rate")
    )
    dev_pct = _pct(
        security.get("dev_team_hold_rate")
        or security.get("creator_balance_rate")
        or stat.get("dev_team_hold_rate")
        or stat.get("creator_hold_rate")
    )
    rug = _f(security.get("rug_ratio") or info.get("rug_ratio") or stat.get("rug_ratio"))
    holders = info.get("holder_count") or stat.get("holder_count")
    try:
        holders = int(holders) if holders is not None else None
    except (TypeError, ValueError):
        holders = None

    sniper = security.get("sniper_count") or tags.get("sniper_count")
    try:
        sniper = int(sniper) if sniper is not None else None
    except (TypeError, ValueError):
        sniper = None

    smart = tags.get("smart_degen_count") or info.get("smart_degen_count")
    try:
        smart = int(smart) if smart is not None else None
    except (TypeError, ValueError):
        smart = None

    volume = _f(
        price_obj.get("volume_1h")
        or price_obj.get("volume_24h")
        or info.get("volume_1h")
        or info.get("volume_24h")
    )

    created = info.get("creation_timestamp") or info.get("open_timestamp")
    age = _age_minutes_from_unix(created)

    # Launchpad / stage heuristic (keep kind=copy for copy-trade rows)
    launchpad = (
        info.get("launchpad_platform")
        or info.get("launchpad")
        or trade.get("launchpad")
    )
    status = info.get("launchpad_status")
    if status == 2 or info.get("migrated_pool"):
        stage = "migrated"
    elif info.get("launchpad_progress") not in (None, "", 1, 1.0) and status == 1:
        stage = "almost"
    else:
        stage = "copy"
    # Don't reclassify copy-wallet hits as trenches — that duplicates swarm work
    # when the same mint also (or only) arrives via copy feeds.
    if trade.get("kind") == "copy":
        kind = "copy"
    else:
        kind = stage

    renounced_mint = security.get("renounced_mint")
    renounced_freeze = security.get("renounced_freeze_account")
    is_honeypot = security.get("is_honeypot")
    is_wash = security.get("is_wash_trading")
    burn = security.get("burn_status")
    creator_status = (
        security.get("creator_token_status")
        or dev.get("creator_token_status")
    )

    flags: list[str] = []
    if is_honeypot in (True, "yes", "true", 1):
        flags.append("honeypot")
    if is_wash in (True, "yes", "true", 1):
        flags.append("wash_trading")
    if renounced_mint is False or renounced_mint in ("no", "false", 0):
        flags.append("mint_not_renounced")
    if renounced_freeze is False or renounced_freeze in ("no", "false", 0):
        flags.append("freeze_not_renounced")
    if rug is not None and rug > 0.3:
        flags.append(f"high_rug_ratio={rug:.2f}")
    if top_pct is not None and top_pct > 60:
        flags.append(f"top10={top_pct:.0f}%")
    if not info and not security:
        flags.append("no_gmgn_data")

    # Quick heuristic label before the LLM swarm
    if "honeypot" in flags or "no_gmgn_data" in flags:
        quick = "fake_or_unknown"
    elif any(f.startswith("high_rug") for f in flags) or "wash_trading" in flags:
        quick = "high_risk"
    elif "mint_not_renounced" in flags or "freeze_not_renounced" in flags:
        quick = "caution"
    elif info:
        quick = "looks_real"
    else:
        quick = "unknown"

    twitter = link.get("twitter_username")
    if twitter and not str(twitter).startswith("http"):
        twitter = f"https://x.com/{twitter}"

    out = {
        **trade,
        "kind": kind,
        "stage": stage,
        "symbol": info.get("symbol") or trade.get("symbol"),
        "name": info.get("name") or trade.get("name") or "",
        "market_cap": mcap,
        "liquidity_usd": liq,
        "volume": volume,
        "volume_h1": _f(price_obj.get("volume_1h")),
        "price": price,
        "price_usd": price,
        "holders": holders,
        "sniper_count": sniper,
        "sniper_pct": None,
        "top_holders_pct": top_pct,
        "dev_holdings_pct": dev_pct,
        "age_minutes": round(age, 2) if age is not None else trade.get("age_minutes"),
        "created_at": _iso_from_unix(created),
        "pool_address": pool.get("pool_address") or info.get("biggest_pool_address"),
        "twitter": twitter or None,
        "telegram": link.get("telegram") or None,
        "website": link.get("website") or None,
        "image_url": info.get("logo") or None,
        "rug_ratio": rug,
        "smart_degen_count": smart,
        "launchpad": launchpad,
        "exchange": pool.get("exchange") or info.get("exchange"),
        "renounced_mint": renounced_mint,
        "renounced_freeze": renounced_freeze,
        "is_honeypot": is_honeypot,
        "is_wash_trading": is_wash,
        "burn_status": burn,
        "creator_token_status": creator_status,
        "bundler_rate": _f(
            security.get("bundler_trader_amount_rate")
            or stat.get("top_bundler_trader_percentage")
        ),
        "insider_rate": _f(
            security.get("suspected_insider_hold_rate")
            or security.get("rat_trader_amount_rate")
            or stat.get("top_rat_trader_percentage")
        ),
        "investigation_flags": flags,
        "investigation_quick": quick,
        "investigation_errors": errors,
        "gmgn_url": f"https://gmgn.ai/sol/token/{mint}",
        "pumpfun_url": f"https://pump.fun/coin/{mint}",
        "dex_url": link.get("gmgn") or f"https://gmgn.ai/sol/token/{mint}",
    }
    # Attach 7d PnL, win rate, phishing, distribution, avg buy MC, analysis score
    return enrich_for_swarm(out)


def copy_wallets_from_config() -> list[str]:
    """Resolved copy-trade wallet list (config + optional GMGN_COPY_WALLETS env)."""
    wallets: list[str] = []
    for w in config.MONITOR_COPY_WALLETS:
        w = (w or "").strip()
        if w and w not in wallets:
            wallets.append(w)
    env = (os.getenv("GMGN_COPY_WALLETS") or "").strip()
    if env:
        for w in env.split(","):
            w = w.strip()
            if w and w not in wallets:
                wallets.append(w)
    return wallets
