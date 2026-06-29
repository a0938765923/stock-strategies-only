"""
持有風控提醒系統 — 每日掃描 → Telegram
==========================================
設計哲學：擇時進出已被證明輸給買進持有（見 honest_multi_backtest.py），
所以本系統不叫你「何時買」，而是幫你「抱得住 + 出事時提醒」。

四種提醒（全部用免費公開資料）：
  ① 大盤轉空     — 0050 跌破 200 日均線（少數能避開大跌的訊號）
  ② 個股急跌/破線 — 持股單日大跌、跌破 60 日季線、距高點回檔
  ③ 處置/注意股   — TWSE 每日公布（就是那課程收 5 萬的免費資料）
  ④ 除權息/法說   — 持股近 14 日內的除權息日、法說會
"""
from __future__ import annotations
import os, sys, time
from datetime import datetime, timedelta
import requests
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from stock_strategies.data import get_price_history

# ── 監控的持股池（可自行增減）──
HOLDINGS = [
    ("2330", "台積電"), ("2454", "聯發科"), ("2317", "鴻海"),
    ("2308", "台達電"), ("2382", "廣達"),  ("3034", "聯詠"),
    ("2881", "富邦金"), ("2882", "國泰金"), ("2603", "長榮"),
    ("0050", "元大台灣50"),
]
HOLD_CODES = {c for c, _ in HOLDINGS}

DROP_PCT       = -4.0   # 單日跌幅警示門檻
NEAR_DAYS      = 14     # 除權息/法說「即將到來」天數
UA             = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def roc_to_date(s: str):
    """民國日期 → date。支援 '1150626' 與 '115/06/29'。"""
    s = str(s).strip().replace("/", "")
    if len(s) == 7 and s.isdigit():
        y = int(s[:3]) + 1911
        return datetime(y, int(s[3:5]), int(s[5:7])).date()
    return None


def iso_to_date(s: str):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


# ═══════════ ① 大盤轉空 ═══════════
def check_market_regime() -> list[str]:
    try:
        df = get_price_history("0050", 2)
        if len(df) < 200:
            return ["⚠️ 大盤資料不足"]
        ma200 = df["close"].rolling(200).mean()
        ma60  = df["close"].rolling(60).mean()
        cl    = df["close"].iloc[-1]
        m200, m60 = ma200.iloc[-1], ma60.iloc[-1]
        prev_cl, prev_m200 = df["close"].iloc[-2], ma200.iloc[-2]
        chg = (cl - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100

        lines = [f"📊 *大盤 (0050)*  {cl:.2f} ({chg:+.2f}%)"]
        if cl < m200:
            lines.append(f"🔴 *已跌破年線 {m200:.1f}* — 空方格局，建議降低持股、停止加碼")
            if prev_cl >= prev_m200:
                lines.append("   ⚡ *今日剛跌破*，轉空訊號")
        elif cl < m60:
            lines.append(f"🟠 跌破季線 {m60:.1f}（年線 {m200:.1f} 仍守）— 留意轉弱")
        else:
            lines.append(f"🟢 站穩年線 {m200:.1f} / 季線 {m60:.1f} — 多方格局，續抱")
        return lines
    except Exception as e:
        return [f"⚠️ 大盤檢查失敗: {str(e)[:40]}"]


# ═══════════ ② 個股急跌/破線 ═══════════
def check_holdings_drop(exdiv_dates: dict | None = None) -> list[str]:
    """只在真有事時報：單日急跌 或 剛跌破季線。
    除息日的機械式下跌標成『除息（非利空）』，不誤報。
    exdiv_dates: {code: ex_date} 近期除息日，用來辨識除息下跌。
    """
    exdiv_dates = exdiv_dates or {}
    today = datetime.now().date()
    alerts = []
    for sid, name in HOLDINGS:
        try:
            df = get_price_history(sid, 1)
            if len(df) < 65:
                continue
            cl   = df["close"].iloc[-1]
            prev = df["close"].iloc[-2]
            chg  = (cl - prev) / prev * 100
            ma60_now  = df["close"].rolling(60).mean().iloc[-1]
            ma60_prev = df["close"].rolling(60).mean().iloc[-2]

            # 是否在除息日附近（±1 日）→ 下跌屬機械式，非利空
            ex = exdiv_dates.get(sid)
            near_exdiv = ex is not None and abs((ex - today).days) <= 1

            flags = []
            if chg <= DROP_PCT:
                if near_exdiv:
                    flags.append(f"單日 {chg:+.1f}%（除息日，非利空）")
                else:
                    flags.append(f"單日 *{chg:+.1f}%* 急跌")
            # 剛跌破季線（昨在線上、今在線下），且非除息造成
            if cl < ma60_now and prev >= ma60_prev and not near_exdiv:
                flags.append(f"跌破季線 {ma60_now:.1f} ⚠️趨勢轉弱")
            if flags:
                alerts.append(f"💥 *{sid} {name}* {cl:.1f} — " + " / ".join(flags))
            time.sleep(0.1)
        except Exception:
            continue
    if not alerts:
        return ["🟢 持股無急跌/破線（正常波動不報）"]
    return alerts


# ═══════════ ③ 處置/注意股 ═══════════
def check_disposition() -> list[str]:
    lines = []
    # 處置股
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/announcement/punish",
                         headers=UA, timeout=15)
        hit = [d for d in r.json() if d.get("Code") in HOLD_CODES]
        if hit:
            for d in hit:
                lines.append(f"🚫 *處置* {d['Code']} {d['Name']} — {d.get('ReasonsOfDisposition','')[:20]}（{d.get('DispositionPeriod','')}）")
    except Exception as e:
        lines.append(f"⚠️ 處置股查詢失敗: {str(e)[:30]}")
    # 注意股
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/announcement/notice",
                         headers=UA, timeout=15)
        hit = [d for d in r.json() if d.get("Code") in HOLD_CODES and d.get("Code")]
        for d in hit:
            lines.append(f"⚠️ *注意* {d['Code']} {d['Name']}")
    except Exception:
        pass
    if not lines:
        return ["🟢 持股無處置/注意"]
    return lines


# ═══════════ ④ 除權息（FinMind）═══════════
def check_dividends() -> tuple[list[str], dict]:
    """回傳 (顯示行, {code: 最近除息日})。除息日字典供急跌偵測辨識機械式下跌。"""
    today = datetime.now().date()
    horizon = today + timedelta(days=NEAR_DAYS)
    alerts = []
    exdiv_map: dict = {}
    for sid, name in HOLDINGS:
        try:
            r = requests.get("https://api.finmindtrade.com/api/v4/data",
                             params={"dataset": "TaiwanStockDividend", "data_id": sid,
                                     "start_date": (today - timedelta(days=90)).isoformat(),
                                     "token": os.environ["FINMIND_TOKEN"]}, timeout=20)
            for row in r.json().get("data", []):
                for fld, label in [("CashExDividendTradingDate", "現金股利"),
                                   ("StockExDividendTradingDate", "股票股利")]:
                    d = iso_to_date(row.get(fld, ""))
                    if not d:
                        continue
                    # 記錄近 ±3 日的除息日（給急跌偵測用）
                    if abs((d - today).days) <= 3:
                        exdiv_map[sid] = d
                    if today <= d <= horizon:
                        cash = row.get("CashEarningsDistribution", 0)
                        alerts.append(f"💰 *{sid} {name}* {label}除息日 *{d}*（配息約 {cash}）")
            time.sleep(0.1)
        except Exception:
            continue
    if not alerts:
        return [f"🟢 近 {NEAR_DAYS} 日無除權息"], exdiv_map
    return alerts, exdiv_map


# ═══════════ ⑤ 法說會（TWSE）═══════════
def check_conferences() -> list[str]:
    today = datetime.now().date()
    horizon = today + timedelta(days=NEAR_DAYS)
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                         headers=UA, timeout=20)
        data = r.json()
        if not data:
            return [f"🟢 近 {NEAR_DAYS} 日無法說會"]
        # 動態找欄位（key 為中文）
        keys = list(data[0].keys())
        code_k = next((k for k in keys if "代號" in k), None)
        name_k = next((k for k in keys if "簡稱" in k or "名稱" in k), None)
        date_k = next((k for k in keys if "日期" in k or "召開" in k), None)
        alerts = []
        for row in data:
            if row.get(code_k) in HOLD_CODES:
                d = roc_to_date(row.get(date_k, "")) if date_k else None
                if d and today <= d <= horizon:
                    alerts.append(f"🎤 *{row.get(code_k)} {row.get(name_k,'')}* 法說會 *{d}*")
        return alerts if alerts else [f"🟢 近 {NEAR_DAYS} 日無法說會"]
    except Exception as e:
        return [f"⚠️ 法說會查詢失敗: {str(e)[:30]}"]


def main():
    if not os.environ.get("FINMIND_TOKEN"):
        print("❌ 缺少 FINMIND_TOKEN"); sys.exit(1)

    today = datetime.now().strftime("%Y/%m/%d (%a)")
    print("="*60)
    print(f"持有風控提醒 — {today}")
    print("="*60)

    # 先抓除權息（同時取得除息日字典，供急跌偵測辨識機械式下跌）
    div_lines, exdiv_map = check_dividends()
    sections = [
        ("①", check_market_regime()),
        ("②", check_holdings_drop(exdiv_map)),
        ("③", check_disposition()),
        ("④", div_lines),
        ("⑤", check_conferences()),
    ]

    msg = [f"🛡️ *每日持有風控* {today}", ""]
    titles = {"①": "大盤格局", "②": "持股急跌/破線", "③": "處置/注意股",
              "④": "除權息提醒", "⑤": "法說會提醒"}
    for tag, lines in sections:
        msg.append(f"*{titles[tag]}*")
        msg.extend(lines)
        msg.append("")
    msg.append("_持有為主、擇時為輔。本訊息為風險提醒，非投資建議_")
    text = "\n".join(msg)

    print(text)
    print("="*60)

    # 推送 Telegram
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            from stock_strategies.notify import send_telegram
            send_telegram(text)
            print("✅ 已推送 Telegram")
        except Exception as e:
            print(f"⚠️ Telegram 推送失敗: {e}")
    else:
        print("⚠️ 未設定 TELEGRAM_*，僅本機輸出")


if __name__ == "__main__":
    main()
