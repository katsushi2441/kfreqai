"""Hyperliquid接続の薄いラッパー。Agent Wallet委任方式(非カストディ)専用。

設計(2026-07-25、ユーザーと合意した方式):
- 資金はユーザー自身のHyperliquidアカウント(account_address)に置いたまま。
- このサーバーは「取引専用・出金不可」のAgent Walletの鍵を生成・保管するだけ。
- Agent Wallet委任(approveAgent)はユーザー自身のメインウォレットの署名が必要な
  アクションなので、このサーバーからは実行できない(SDKのExchange.approve_agent()は
  self.wallet=メインウォレットの鍵を要求する)。よってPhase1は、生成したAgent
  Walletのアドレスをユーザーに提示し、Hyperliquid公式UI(API管理画面)で
  ユーザー自身に承認してもらう運用(メインウォレットの鍵は一切このサーバーに渡らない)。

発注(実弾)はHL_LIVE_TRADING=1を明示的に立てない限りDRY_RUNで、実際の注文は
送信しない(place_order()はNotImplementedErrorを投げる)。Testnetでの検証が
終わるまでのガード。
"""
import os

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL

LIVE_TRADING = os.environ.get("HL_LIVE_TRADING", "0") == "1"
USE_TESTNET = os.environ.get("HL_USE_TESTNET", "1") == "1"
API_URL = TESTNET_API_URL if USE_TESTNET else MAINNET_API_URL

# モックモード(HL_MOCK=1): Hyperliquidに一切通信せず、シミュレーションの口座状態を
# 返す。testnet faucetは「本番で最低10 USDC入金済みのアドレス」しか使えないという
# 仕様上の制約があり、残高ゼロのウォレットでは実通信の検証すらできないため、
# 製品の全体(UI/チャット/パラメータ/発注ロジック)を金ゼロで検証するための開発モード。
# ★これはあくまでシミュレーション。実際のHyperliquid通信の検証にはならない。
# 返り値には必ず mock=True を含め、画面でも「シミュレーション」と明示する
# (偽デモ禁止のルール: 本物と誤認させない)。
MOCK = os.environ.get("HL_MOCK", "0") == "1"
_MOCK_EQUITY_USD = float(os.environ.get("HL_MOCK_EQUITY_USD", "1000"))


def generate_agent_wallet():
    """新しいAgent Wallet鍵ペアを生成する。純粋な暗号処理でHyperliquidとの通信は
    発生しない(ユーザーの資金にもメインウォレットにも一切触れない)。"""
    account = eth_account.Account.create()
    return {"address": account.address, "private_key": account.key.hex()}


# Info()コンストラクタは生成のたびに spotMeta/meta をネット取得する。毎fetchで
# 作り直すと無駄な通信でレート制限(429)を招くため、URLごとに1つだけ生成して使い回す。
_INFO_CLIENTS = {}


def info_client():
    cli = _INFO_CLIENTS.get(API_URL)
    if cli is None:
        cli = _INFO_CLIENTS[API_URL] = Info(API_URL, skip_ws=True)
    return cli


def mainnet_info_client():
    """常にmainnetを読む公開クライアント。builder-dex(xyz等)のFX/商品/指数は
    testnetに価格フィードが無く、mainnetにしか履歴が無いため、そのcandle取得に使う
    (読み取り専用・認証不要・資金不要)。生成コストが高いので使い回す。"""
    cli = _INFO_CLIENTS.get(MAINNET_API_URL)
    if cli is None:
        cli = _INFO_CLIENTS[MAINNET_API_URL] = Info(MAINNET_API_URL, skip_ws=True)
    return cli


def get_account_snapshot(main_wallet_address):
    """ユーザーのメインアカウントの状態を読み取り専用で取得する。
    Hyperliquidの/infoは公開エンドポイントで認証不要(誰のアドレスでも読める)。"""
    if MOCK:
        return {
            "account_value_usd": _MOCK_EQUITY_USD,
            "total_margin_used_usd": 0.0,
            "positions": [],
            "withdrawable_usd": _MOCK_EQUITY_USD,
            "mock": True,
        }
    info = info_client()
    state = info.user_state(main_wallet_address)
    margin = state.get("marginSummary") or {}
    positions = [
        p["position"] for p in (state.get("assetPositions") or [])
        if float((p.get("position") or {}).get("szi") or 0) != 0
    ]
    return {
        "account_value_usd": float(margin.get("accountValue") or 0),
        "total_margin_used_usd": float(margin.get("totalMarginUsed") or 0),
        "positions": positions,
        "withdrawable_usd": float(state.get("withdrawable") or 0),
    }


def get_open_orders(main_wallet_address):
    if MOCK:
        return []
    return info_client().open_orders(main_wallet_address)


def agent_is_approved_onchain(main_wallet_address, agent_address):
    """メイン口座に、そのAgentアドレスが実際に委任登録されているかをオンチェーンで
    確認する(extraAgents)。自己申告ボタンでなく実状態を見るための関数。
    モック時はTrue(検証をスキップ)。"""
    if MOCK:
        return True
    import urllib.request
    import json as _json
    body = _json.dumps({"type": "extraAgents", "user": main_wallet_address}).encode()
    req = urllib.request.Request(API_URL + "/info", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        agents = _json.loads(resp.read()) or []
    target = (agent_address or "").lower()
    for a in agents:
        if str(a.get("address", "")).lower() == target:
            return True
    return False


def _positions_from_state(state):
    """assetPositions -> kfreqaiの保有ポジション表に合わせた行の配列。"""
    out = []
    for ap in (state.get("assetPositions") or []):
        pos = ap.get("position") or {}
        szi = float(pos.get("szi") or 0)
        if szi == 0:
            continue
        lev = (pos.get("leverage") or {})
        out.append({
            "coin": pos.get("coin"),
            "is_short": szi < 0,
            "size": abs(szi),
            "entry_px": float(pos.get("entryPx") or 0),
            "position_value_usd": float(pos.get("positionValue") or 0),
            "unrealized_pnl_usd": float(pos.get("unrealizedPnl") or 0),
            "return_on_equity": float(pos.get("returnOnEquity") or 0),
            "leverage": lev.get("value"),
            "liquidation_px": float(pos.get("liquidationPx") or 0) if pos.get("liquidationPx") else None,
        })
    return out


def get_dashboard(main_wallet_address, fills_limit=50, daily_days=7):
    """kfreqai本番サマリ相当を1回で返す: 残高/保有ポジション/約定履歴/日次損益。
    すべてHyperliquidの読み取り専用API(公開)から。モック時は残高だけ擬似値で
    ポジション・約定は空(まだ取引していない、という正直な状態)。"""
    import datetime
    if MOCK:
        return {"mock": True, "account_value_usd": _MOCK_EQUITY_USD,
                "withdrawable_usd": _MOCK_EQUITY_USD, "unrealized_pnl_usd": 0.0,
                "positions": [], "fills": [], "daily": [],
                "closed_pnl_total_usd": 0.0, "fills_count": 0}
    info = info_client()
    state = info.user_state(main_wallet_address)
    margin = state.get("marginSummary") or {}
    positions = _positions_from_state(state)
    # Unified Account: 担保はspotのUSDCと共通。perpのaccountValueだけ見ると0に
    # 見えるので、spotのUSDC残高を合算して「使える資金(equity)」とする。
    spot_usdc = 0.0
    try:
        spot = info.spot_user_state(main_wallet_address)
        for b in (spot.get("balances") or []):
            if b.get("coin") == "USDC":
                spot_usdc = float(b.get("total") or 0)
                break
    except Exception:
        pass
    perp_value = float(margin.get("accountValue") or 0)
    fills = info.user_fills(main_wallet_address) or []
    # 新しい順に。約定履歴(最新fills_limit件)
    fills_sorted = sorted(fills, key=lambda f: f.get("time") or 0, reverse=True)
    recent = [{
        "coin": f.get("coin"), "side": "sell" if f.get("side") == "A" else "buy",
        "dir": f.get("dir"), "px": float(f.get("px") or 0), "sz": float(f.get("sz") or 0),
        "closed_pnl_usd": float(f.get("closedPnl") or 0), "fee_usd": float(f.get("fee") or 0),
        "time_ms": int(f.get("time") or 0),
    } for f in fills_sorted[:fills_limit]]
    # 日次損益(直近daily_days日・JST): closedPnlをJSTの日付で集計
    jst = datetime.timezone(datetime.timedelta(hours=9))
    byday = {}
    for f in fills:
        t = int(f.get("time") or 0)
        if not t:
            continue
        d = datetime.datetime.fromtimestamp(t / 1000, jst).date().isoformat()
        e = byday.setdefault(d, {"date": d, "abs_profit": 0.0, "trade_count": 0})
        e["abs_profit"] += float(f.get("closedPnl") or 0)
        e["trade_count"] += 1
    daily = sorted(byday.values(), key=lambda x: x["date"], reverse=True)[:daily_days]
    closed_total = sum(float(f.get("closedPnl") or 0) for f in fills)
    unrealized = sum(p["unrealized_pnl_usd"] for p in positions)
    return {"mock": False,
            # Unified Account: 証拠金はspot USDCと共通(cross担保)なので、perp証拠金を
            # spotに足すと二重計上になる。総資産 = spot USDC + 含み損益(未実現)。
            "account_value_usd": spot_usdc + unrealized,
            "perp_margin_used_usd": perp_value,
            "spot_usdc": spot_usdc,
            "withdrawable_usd": float(state.get("withdrawable") or 0),
            "unrealized_pnl_usd": unrealized,
            "positions": positions, "fills": recent, "daily": daily,
            "closed_pnl_total_usd": closed_total, "fills_count": len(fills)}


def agent_exchange(agent_private_key, main_wallet_address):
    """発注に使うExchangeインスタンス。walletはAgent Wallet(取引専用・出金不可)、
    account_addressがユーザーの実口座(資金はここに残る)。"""
    signer = eth_account.Account.from_key(agent_private_key)
    return Exchange(signer, base_url=API_URL, account_address=main_wallet_address)


def place_order(agent_private_key, main_wallet_address, coin, is_buy, size, leverage=None):
    """成行でエントリー。Hyperliquidは成行=IoCの積極Limit(market_open)。
    order()直呼びはlimit_px/order_typeが要るためmarket_openを使う。
    モック時はシミュレーション約定で実注文は飛ばない。"""
    if MOCK:
        return {"status": "ok", "mock": True, "simulated_order":
                {"coin": coin, "is_buy": is_buy, "size": size}}
    if not LIVE_TRADING:
        raise NotImplementedError(
            "発注実行はまだ有効化されていません(HL_LIVE_TRADING=0)。")
    exchange = agent_exchange(agent_private_key, main_wallet_address)
    if leverage:
        try:
            exchange.update_leverage(int(leverage), coin, is_cross=True)
        except Exception:
            pass  # レバ設定失敗でも既定レバで発注は試みる
    return exchange.market_open(coin, is_buy, size)


def order_fill_info(res):
    """market_open/order の応答から (filled_bool, detail) を返す。
    成功: {'status':'ok','response':{'data':{'statuses':[{'filled':{...}}]}}}
    失敗: statuses[0]が{'error': '...'} 等。モックは常にfilled扱い。"""
    if not isinstance(res, dict):
        return False, "no response"
    if res.get("mock"):
        return True, "mock"
    if res.get("status") != "ok":
        return False, str(res.get("response") or res)[:200]
    try:
        statuses = res["response"]["data"]["statuses"]
        s0 = statuses[0] if statuses else {}
    except Exception:
        return False, "unexpected response shape"
    if "filled" in s0:
        return True, s0["filled"]
    if "resting" in s0:
        return True, s0["resting"]
    return False, str(s0)[:200]


def close_position(agent_private_key, main_wallet_address, coin, size=None, is_short=None):
    """保有ポジションを成行クローズ。market_closeがポジションの向き・数量を
    自動検出して反対売買する(sz省略で全クローズ)。"""
    if MOCK:
        return {"status": "ok", "mock": True, "simulated_close": {"coin": coin, "size": size}}
    if not LIVE_TRADING:
        raise NotImplementedError(
            "決済実行はまだ有効化されていません(HL_LIVE_TRADING=0)。")
    exchange = agent_exchange(agent_private_key, main_wallet_address)
    return exchange.market_close(coin)
