"""盤中停損/停利警報 — 每 15 分鐘跑

讀 Sheet 的 Positions 分頁（status=HOLDING 的部位），用 Yahoo Finance
（延遲約 15~20 分鐘）抓現價，觸發任一條件就推 Telegram：

  🔴 STOP_HIT   — 觸及或跌破停損價
  🟡 NEAR_STOP  — 距離停損 < 2%
  🎯 TARGET_HIT — 觸及或漲過目標價
  ⚡ BIG_DROP   — 單日下跌 > 5%

避免轟炸：同一部位、同種警報 2 小時內不重複推。

執行: uv run python intraday_monitor.py
"""

import json
import os
import sys
from datetime import datetime, timedelta

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.sheet import get_gsheet
from stock_strategies.notify import send_telegram
import gspread


REQUIRED_ENV = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
]

POS_HEADERS = [
    "stock_id", "name", "entry_date", "entry_price",
    "stop_loss", "target_price", "shares",
    "status", "last_alert_type", "last_alert_at", "notes",
]

NEAR_STOP_PCT = 0.02   # 距離停損 < 2% → 黃色預警
BIG_DROP_PCT = -0.05   # 單日 -5% → 緊急
ALERT_COOLDOWN_HOURS = 2  # 同警報 2 小時內不重複


def yahoo_price(stock_id: str) -> dict | None:
    """從 Yahoo 抓台股最新價（15~20 分鐘延遲）— 作為 twstock 的備援。
    依序試 .TW (上市) → .TWO (上櫃)。失敗回 None。
    """
    for suffix in (".TW", ".TWO"):
        symbol = f"{stock_id}{suffix}"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        try:
            r = requests.get(
                url, timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("chart", {}).get("result")
            if not results:
                continue
            meta = results[0].get("meta", {})
            cur = meta.get("regularMarketPrice")
            prev = meta.get("previousClose") or meta.get("chartPreviousClose")
            if cur is None or prev is None or prev <= 0:
                continue
            return {
                "symbol": symbol,
                "current": float(cur),
                "previous_close": float(prev),
                "day_change_pct": (float(cur) - float(prev)) / float(prev),
                "source": "yahoo",
            }
        except Exception:
            continue
    return None


def twstock_batch_prices(stock_ids: list[str]) -> dict[str, dict]:
    """一次批次抓多檔即時報價（TWSE 公開 API，延遲 1~2 分鐘）。
    回 {stock_id: {current, previous_close, day_change_pct, source}}
    抓不到的就不會在 dict 裡，呼叫端要自己 fallback 到 yahoo_price。
    """
    if not stock_ids:
        return {}
    try:
        import twstock
        # list 形式呼叫一律回傳 {sid: {...}}，不需額外包一層
        raw = twstock.realtime.get(list(stock_ids))
    except Exception as e:
        print(f"⚠️ twstock 批次失敗: {str(e)[:80]}", file=sys.stderr)
        return {}

    out = {}
    for sid, data in raw.items():
        if not isinstance(data, dict) or not data.get("success"):
            continue
        rt = data.get("realtime", {})
        cur_s = rt.get("latest_trade_price", "-")
        opn_s = rt.get("open", "-")
        if cur_s in ("-", "", None):
            continue
        try:
            cur = float(cur_s)
            # 用「今日開盤」作為日內變化的基準（盤中監控核心是現價 vs 停損/停利）
            prev = float(opn_s) if opn_s not in ("-", "", None) else cur
        except (TypeError, ValueError):
            continue
        out[str(sid).strip()] = {
            "symbol": sid,
            "current": cur,
            "previous_close": prev,
            "day_change_pct": (cur - prev) / prev if prev > 0 else 0,
            "source": "twstock",
        }
    return out


def fetch_price(stock_id: str) -> dict | None:
    """主入口：twstock 為主、Yahoo 為備援。
    （單檔查詢；批次請用 twstock_batch_prices 較有效率）
    """
    batch = twstock_batch_prices([stock_id])
    if stock_id in batch:
        return batch[stock_id]
    return yahoo_price(stock_id)


def read_positions(ws) -> tuple[list[dict], dict[int, dict]]:
    """讀 Positions 分頁，回傳:
       holdings (status=HOLDING 的 list)
       row_index_map (stock_id → row number for updates)
    """
    rows = ws.get_all_records()
    holdings = []
    row_map = {}
    for i, r in enumerate(rows, start=2):
        if str(r.get("status", "")).upper() != "HOLDING":
            continue
        holdings.append(r)
        row_map[str(r.get("stock_id", "")).strip()] = i
    return holdings, row_map


def _to_float(v, default=None):
    if v in (None, "", "—"):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _alert_allowed(pos: dict, alert_type: str) -> bool:
    """檢查冷卻時間：同部位 + 同警報，2 小時內只推一次。"""
    last_type = str(pos.get("last_alert_type", "")).strip()
    last_at = str(pos.get("last_alert_at", "")).strip()
    if last_type != alert_type or not last_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_at)
        return datetime.now() - last_dt > timedelta(hours=ALERT_COOLDOWN_HOURS)
    except ValueError:
        return True


def evaluate_position(pos: dict, price: dict) -> list[tuple[str, str]]:
    """依現價判斷該部位要發哪些警報。
    回 list of (alert_type, message_lines)，可能多於 1 個。
    """
    alerts = []
    entry = _to_float(pos.get("entry_price"))
    stop = _to_float(pos.get("stop_loss"))
    target = _to_float(pos.get("target_price"))
    cur = price["current"]
    pnl_pct = (cur - entry) / entry if entry else None

    common = (
        f"*{pos['stock_id']} {pos['name']}*\n"
        f"現價 {cur:.2f}（前收 {price['previous_close']:.2f}, "
        f"今日 {price['day_change_pct']*100:+.2f}%）\n"
        f"進場 {entry:.2f}, 未實現 {(pnl_pct*100):+.2f}%\n"
        f"停損 {stop:.2f} / 目標 {target:.2f}\n"
    )

    # 1. 跌破停損
    if stop and cur <= stop:
        msg = "🔴 *觸及停損 — 建議立刻出場*\n" + common
        msg += "_停損紀律 > 凹單，馬上掛賣單_"
        alerts.append(("STOP_HIT", msg))

    # 2. 接近停損（但還沒跌破）
    elif stop and 0 < (cur - stop) / stop < NEAR_STOP_PCT:
        msg = "🟡 *接近停損預警*\n" + common
        msg += f"_距離停損僅 {((cur - stop)/stop)*100:.2f}%，準備好停損動作_"
        alerts.append(("NEAR_STOP", msg))

    # 3. 觸及停利
    if target and cur >= target:
        msg = "🎯 *觸及停利 — 建議分批出場*\n" + common
        msg += "_先出一半鎖利、剩半部移動停損抱波段_"
        alerts.append(("TARGET_HIT", msg))

    # 4. 單日異常下跌
    if price["day_change_pct"] <= BIG_DROP_PCT:
        msg = "⚡ *單日異常下跌警告*\n" + common
        msg += f"_今日跌 {price['day_change_pct']*100:.2f}%，可能黑天鵝事件，立即評估_"
        alerts.append(("BIG_DROP", msg))

    return alerts


def update_alert_log(ws, row_num: int, alert_type: str):
    """記錄這個部位最後一次警報的類型與時間，方便冷卻時間判斷。"""
    now = datetime.now().isoformat(timespec="seconds")
    # last_alert_type = col 9 (I), last_alert_at = col 10 (J)
    try:
        ws.update_cell(row_num, 9, alert_type)
        ws.update_cell(row_num, 10, now)
    except Exception as e:
        print(f"⚠️ 更新 alert log 失敗: {e}", file=sys.stderr)


def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    sh = get_gsheet()
    try:
        ws = sh.worksheet("Positions")
    except gspread.WorksheetNotFound:
        print("⚠️ 找不到 Positions 分頁，跳過監控")
        return

    holdings, row_map = read_positions(ws)
    print(f"[{datetime.now()}] 持有中部位: {len(holdings)}")
    if not holdings:
        print("✅ 沒有 HOLDING 部位，無需監控")
        return

    # 1. 一次批次抓所有部位的價格（twstock 為主）
    all_ids = [str(pos["stock_id"]).strip() for pos in holdings]
    batch = twstock_batch_prices(all_ids)
    print(f"  twstock 批次抓到 {len(batch)}/{len(all_ids)} 檔")

    triggered = 0
    for pos in holdings:
        sid = str(pos["stock_id"]).strip()
        print(f"檢查 {sid} {pos.get('name', '')}...")

        # 優先用 twstock 即時，失敗 fallback yahoo
        price = batch.get(sid) or yahoo_price(sid)
        if not price:
            print(f"  ⚠️ 抓不到價格")
            continue
        print(f"  資料源: {price.get('source', '?')}")

        alerts = evaluate_position(pos, price)
        if not alerts:
            print(f"  現價 {price['current']:.2f}, 今日 {price['day_change_pct']*100:+.2f}% — 安全")
            continue

        # 同一波只推「最緊急」的（按優先序選一個）
        priority = {"STOP_HIT": 0, "BIG_DROP": 1, "TARGET_HIT": 2, "NEAR_STOP": 3}
        alerts.sort(key=lambda a: priority.get(a[0], 99))

        for atype, message in alerts:
            if not _alert_allowed(pos, atype):
                print(f"  {atype} 在冷卻時間內，跳過")
                continue
            send_telegram(message)
            update_alert_log(ws, row_map[sid], atype)
            print(f"  ✅ 推送 {atype}")
            triggered += 1
            break  # 一個部位只發一個（最緊急的）

    print(f"\n[{datetime.now()}] 完成，共觸發 {triggered} 則警報")


if __name__ == "__main__":
    main()
