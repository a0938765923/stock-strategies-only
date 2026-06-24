"""今日開盤進場建議

08:30 排程跑（盤前 30 分鐘），基於昨日 14:30 訊號 + 今晨夜盤狀況，
產出 ✅進場 / ⚠️暫緩 / ❌取消 三類清單，含進出場價、停損價、目標價。
打開 Telegram 看訊息就能直接下單，不用再做判斷。

執行: uv run python morning_entry.py
"""

import os
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.night_session import get_night_session
from stock_strategies.sheet import read_latest_signals
from stock_strategies.notify import send_telegram
from stock_strategies.config import CONFIG


REQUIRED_ENV = [
    "FINMIND_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
]


def make_decision(action: str, night_bias: str, night_pct: float) -> tuple[str, str, str]:
    """根據昨日 action 與今晨夜盤狀況，產出明確的建議。

    回傳 (emoji, label, reason)，emoji 用來分類：
        ✅ → 直接進場
        ⚠️ → 暫緩或試單
        ❌ → 今日取消
    """
    big_drop = CONFIG.get("night_gap_big", 1.5)
    act = action.upper()

    if act == "BUY":
        if night_bias == "bull":
            return "✅", "建議進場", "夜盤順風，BUY 訊號維持"
        if night_bias == "flat":
            return "✅", "建議進場", "夜盤中性，BUY 訊號維持"
        if night_pct <= -big_drop:
            return "❌", "取消進場", f"夜盤重挫 {night_pct:+.1f}%，今日避險空手"
        return "⚠️", "暫緩進場", f"夜盤逆風 {night_pct:+.1f}%，等開盤止穩再決定"

    if act == "WATCH":
        if night_bias == "bull":
            return "⚠️", "可小部位試單", "夜盤順風，WATCH 可考慮 2-3% 倉位試單"
        if night_bias == "flat":
            return "⚠️", "繼續觀察", "夜盤中性，WATCH 維持觀望、不急著進"
        if night_pct <= -big_drop:
            return "❌", "今日取消", f"夜盤重挫 {night_pct:+.1f}%，WATCH 一律避險"
        return "⚠️", "繼續觀察", "夜盤逆風，WATCH 暫不進場"

    return "❌", "非進場訊號", "昨日 action 非 BUY/WATCH"


def _fmt_num(v) -> str:
    """安全格式化數字（Sheet 讀回來可能是 str 或 float）"""
    if v in (None, "", "—"):
        return "—"
    try:
        f = float(v)
        if abs(f) >= 100:
            return f"{f:,.0f}"
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return str(v)


def format_entry_message(night: dict | None, signals: list[dict]) -> str:
    """格式化今日開盤建議訊息"""
    today = datetime.now()
    wd = "一二三四五六日"[today.weekday()]
    lines = [
        f"🔔 *今日開盤進場建議* {today.strftime('%Y/%m/%d')} (週{wd})",
        "",
    ]

    # === 夜盤摘要 ===
    if night:
        lines.append(
            f"{night['emoji']} 夜盤 *{night['pct']:+.2f}%* "
            f"({night['spread']:+.0f} 點 · {night['label']})"
        )
        lines.append(f"📊 {night['direction']}")
        bias = night["bias"]
        pct = night["pct"]
    else:
        lines.append("⚠️ *夜盤資料取不到* — 預設保守模式")
        bias = "bear"
        pct = -3.0
    lines.append("")

    # === 過濾最近一批 BUY/WATCH ===
    actionable = [
        s for s in signals
        if str(s.get("action", "")).upper() in ("BUY", "WATCH")
    ]
    if not actionable:
        lines.append("📋 _昨日無 BUY/WATCH 訊號_")
        lines.append("→ *今日空手等新訊號*")
        return "\n".join(lines)

    latest_day = actionable[0].get("date", "")
    batch = [s for s in actionable if s.get("date", "") == latest_day]

    enters: list[dict] = []
    delays: list[dict] = []
    skips: list[dict] = []
    for s in batch:
        action = str(s.get("action", "")).upper()
        emoji, label, reason = make_decision(action, bias, pct)
        item = {
            "stock_id": s.get("stock_id", ""),
            "name": s.get("name", ""),
            "score": s.get("signal_score", ""),
            "entry": _fmt_num(s.get("entry_price")),
            "stop": _fmt_num(s.get("stop_loss_price")),
            "target": _fmt_num(s.get("target_price")),
            "rr": _fmt_num(s.get("rr_ratio")),
            "reason": reason,
            "label": label,
        }
        if emoji == "✅":
            enters.append(item)
        elif emoji == "⚠️":
            delays.append(item)
        else:
            skips.append(item)

    lines.append(f"📅 _基於 {latest_day} 收盤訊號分析_")
    lines.append("")

    # === ✅ 建議進場 ===
    lines.append(f"✅ *建議進場* ({len(enters)} 檔)")
    if enters:
        for it in enters:
            lines.append(
                f"• *{it['stock_id']} {it['name']}* @ {it['entry']}"
            )
            lines.append(
                f"  停損 {it['stop']} / 目標 {it['target']} (R/R {it['rr']})"
            )
        lines.append("_單檔建議 5% 倉位以下、總投入不超過 30%_")
    else:
        lines.append("_今日無建議進場標的_")
    lines.append("")

    # === ⚠️ 暫緩 ===
    if delays:
        lines.append(f"⚠️ *暫緩 / 觀察* ({len(delays)} 檔)")
        for it in delays:
            lines.append(
                f"• {it['stock_id']} {it['name']} — _{it['label']}_"
            )
            lines.append(f"  ↳ {it['reason']}")
        lines.append("")

    # === ❌ 取消 ===
    if skips:
        lines.append(f"❌ *今日取消* ({len(skips)} 檔)")
        for it in skips:
            lines.append(f"• {it['stock_id']} {it['name']} — _{it['label']}_")
        lines.append("")

    # === 操作提醒 ===
    lines.append("━━━━━━━━━━")
    lines.append("📝 *操作 SOP*")
    lines.append("• 09:15~09:30 用進場價*限價*掛單（別追第一根 K）")
    lines.append("• 進場成功立刻設*停損單*（券商 App 內可設）")
    lines.append("• 跌破停損自動出，*不要凹單*")
    lines.append("• 漲到目標價可分批出脫（先出一半）")
    lines.append("• 連續 3 次停損 → 強制休息檢討")

    return "\n".join(lines)


def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    print(f"[{datetime.now()}] 取得今晨夜盤...")
    night = get_night_session()
    if night:
        print(f"  → {night['date']} 夜盤 {night['pct']:+.2f}% ({night['label']})")
    else:
        print("  → 夜盤資料暫時取不到")

    print("讀取昨日訊號...")
    try:
        signals = read_latest_signals(limit=300)
    except Exception as e:
        print(f"⚠️ 讀取訊號失敗: {e}", file=sys.stderr)
        signals = []
    print(f"  → {len(signals)} 筆")

    print("發送 Telegram 進場建議...")
    send_telegram(format_entry_message(night, signals))
    print("✅ 完成")


if __name__ == "__main__":
    main()
