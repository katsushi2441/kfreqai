"""MEXC先物APIキーの疎通・発注可否テスト(履歴で合意した15分ゲート)。

使い方:
  1) kurage-mexcf/.env に MEXCF_API_KEY / MEXCF_API_SECRET を置く(チャットに貼らない)
  2) 読み取りテスト:  set -a; . ./.env; set +a; python3 key_test.py
  3) 発注テスト(実弾・極小): 上に加えて MEXCF_LIVE=1 python3 key_test.py --order
     - BTC_USDTに最小サイズのショートを成行で建て、直後に成行で決済する。
     - これが通れば「一般口座でも先物API発注が可能」= 実装GO。
     - code=602/603系や権限エラーなら「口座がAPI発注を許可されていない」= MEXC側の壁。
"""
import json
import sys
import time

import mexcf_client as mx


def main():
    do_order = "--order" in sys.argv
    print("=== 1) 公開API(認証不要) ===")
    d = mx.contract_detail("BTC_USDT")
    print("  BTC_USDT state:", d.get("state"), "| apiAllowed:", d.get("apiAllowed"),
          "| contractSize:", d.get("contractSize"), "| minVol:", d.get("minVol"))
    t = mx.ticker("BTC_USDT")
    px = float(t.get("lastPrice") or 0)
    print("  lastPrice:", px)

    print("=== 2) 認証読み取り(キー有効性・先物権限) ===")
    try:
        assets = mx.account_assets()
    except mx.MexcfError as exc:
        print("  ✗ 読み取り失敗:", exc)
        print("  → キー未設定/権限不足/署名不一致のどれか。ここで停止。")
        return 1
    usdt = next((a for a in (assets or []) if a.get("currency") == "USDT"), {})
    print("  ✓ assets取得OK。USDT: available=%s equity=%s" % (
        usdt.get("availableBalance"), usdt.get("equity")))
    pos = mx.open_positions()
    print("  open_positions:", len(pos or []), "件")

    if not do_order:
        print("=== 3) 発注テストは未実行(--order と MEXCF_LIVE=1 で実行) ===")
        print("読み取りは通っています。発注可否の最終確認は --order を付けて実行してください。")
        return 0

    print("=== 3) 極小テスト発注(実弾): BTC_USDT ショート建て→即決済 ===")
    vol = float(d.get("minVol") or 1)  # 最小口数
    oid = "kfx-test-%d" % int(time.time())
    try:
        r = mx.submit_order("BTC_USDT", mx.SIDE_OPEN_SHORT, vol, leverage=2,
                            order_type=mx.ORDER_TYPE_MARKET, external_oid=oid)
        print("  ✓ ショート建て受理:", json.dumps(r, ensure_ascii=False)[:200])
    except mx.MexcfError as exc:
        print("  ✗ 発注拒否:", exc)
        print("  → これが『MEXC側の壁』かを判断する材料。エラーコードを確認してください。")
        return 2
    time.sleep(2)
    try:
        r2 = mx.submit_order("BTC_USDT", mx.SIDE_CLOSE_SHORT, vol, leverage=2,
                             order_type=mx.ORDER_TYPE_MARKET, external_oid=oid + "c")
        print("  ✓ 決済受理:", json.dumps(r2, ensure_ascii=False)[:200])
    except mx.MexcfError as exc:
        print("  ⚠ 決済失敗(建玉が残っている可能性。MEXCの画面で確認を):", exc)
        return 3
    print("=== 結論: 発注APIは開通。MEXC先物ショートの実装GO ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
