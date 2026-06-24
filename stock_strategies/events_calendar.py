"""重大事件日曆 — 14 天內事件警示

包含：
  - NVIDIA GTC / 法說（AI 族群）
  - 美國 FOMC 利率決議（大盤）
  - 台積電法說（半導體）
  - 美國 CPI/PPI 公布（全市場）
  - 台股月營收公布（每月 10 日前後）
  - 除權息日（高殖利率股）

2026 已知大事件預先寫死，CPI/PPI/FOMC 用美國規律推算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


@dataclass
class Event:
    when: date
    name: str
    impact: str
    affected_tags: list[str] = field(default_factory=list)
    severity: str = "medium"  # low / medium / high

    def days_from(self, today: date) -> int:
        return (self.when - today).days


# === 2026 已知重大事件 ===
EVENTS_2026 = [
    # ── 美國貨幣政策（影響全市場）──
    Event(date(2026, 1, 28), "美國 FOMC 利率決議 #1", "全市場", ["MACRO"], "high"),
    Event(date(2026, 3, 18), "美國 FOMC 利率決議 #2", "全市場", ["MACRO"], "high"),
    Event(date(2026, 4, 29), "美國 FOMC 利率決議 #3", "全市場", ["MACRO"], "high"),
    Event(date(2026, 6, 17), "美國 FOMC 利率決議 #4", "全市場", ["MACRO"], "high"),
    Event(date(2026, 7, 29), "美國 FOMC 利率決議 #5", "全市場", ["MACRO"], "high"),
    Event(date(2026, 9, 16), "美國 FOMC 利率決議 #6", "全市場", ["MACRO"], "high"),
    Event(date(2026, 11, 4), "美國 FOMC 利率決議 #7", "全市場", ["MACRO"], "high"),
    Event(date(2026, 12, 16), "美國 FOMC 利率決議 #8", "全市場", ["MACRO"], "high"),

    # ── 美國 CPI（每月中旬左右）──
    Event(date(2026, 1, 14), "美國 12 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 2, 11), "美國 1 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 3, 11), "美國 2 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 4, 14), "美國 3 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 5, 13), "美國 4 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 6, 11), "美國 5 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 7, 15), "美國 6 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 8, 12), "美國 7 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 9, 11), "美國 8 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 10, 15), "美國 9 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 11, 13), "美國 10 月 CPI", "全市場", ["MACRO"], "medium"),
    Event(date(2026, 12, 10), "美國 11 月 CPI", "全市場", ["MACRO"], "medium"),

    # ── NVIDIA（AI 族群連動）──
    Event(date(2026, 3, 17), "NVIDIA GTC 2026 開幕", "AI 族群", ["AI"], "high"),
    Event(date(2026, 2, 26), "NVIDIA Q4 法說", "AI 族群", ["AI"], "high"),
    Event(date(2026, 5, 28), "NVIDIA Q1 法說", "AI 族群", ["AI"], "high"),
    Event(date(2026, 8, 27), "NVIDIA Q2 法說", "AI 族群", ["AI"], "high"),
    Event(date(2026, 11, 19), "NVIDIA Q3 法說", "AI 族群", ["AI"], "high"),

    # ── 台積電法說（半導體連動）──
    Event(date(2026, 1, 16), "台積電 Q4 法說", "半導體", ["SEMI", "2330"], "high"),
    Event(date(2026, 4, 17), "台積電 Q1 法說", "半導體", ["SEMI", "2330"], "high"),
    Event(date(2026, 7, 17), "台積電 Q2 法說", "半導體", ["SEMI", "2330"], "high"),
    Event(date(2026, 10, 16), "台積電 Q3 法說", "半導體", ["SEMI", "2330"], "high"),

    # ── 月營收公布（每月 10 日前後，個股影響大）──
    # 用月份初寫一筆代表「該月 10 日左右」
    *[Event(date(2026, m, 10), f"台股 {m-1 if m>1 else 12} 月營收公布", "個股", ["REVENUE"], "medium")
      for m in range(1, 13)],

    # ── 主要除權息日（高殖利率股 7~8 月集中）──
    Event(date(2026, 6, 18), "台積電除息", "個股", ["2330"], "high"),
    Event(date(2026, 7, 25), "鴻海除息", "個股", ["2317"], "high"),
    Event(date(2026, 7, 22), "聯發科除息", "個股", ["2454"], "high"),
    Event(date(2026, 8, 6), "0050 除息", "ETF", ["0050"], "high"),
    Event(date(2026, 7, 17), "0056 除息", "ETF", ["0056"], "high"),
    Event(date(2026, 6, 19), "00929 除息", "ETF", ["00929"], "high"),
]


# 把 tag 對應到股號（用於 per-stock 警示）
SEMI_STOCKS = {"2330", "2454", "3661", "3035", "2379", "5274", "3037", "2449"}
AI_STOCKS = {"2330", "2382", "2376", "6669", "3231", "3017", "3653", "8210", "3661", "3035",
             "5274", "2308"}


def get_upcoming_events(within_days: int = 14, today: date | None = None) -> list[Event]:
    """近 within_days 天內的事件（含今天）"""
    today = today or datetime.now().date()
    return [e for e in EVENTS_2026 if 0 <= e.days_from(today) <= within_days]


def relevant_events_for_stock(stock_id: str, within_days: int = 14) -> list[Event]:
    """跟某檔個股相關的近期事件"""
    today = datetime.now().date()
    sid = str(stock_id).strip()
    relevant = []
    for e in get_upcoming_events(within_days, today):
        # 直接命中 stock_id
        if sid in e.affected_tags:
            relevant.append(e)
            continue
        # 透過族群 tag
        if "AI" in e.affected_tags and sid in AI_STOCKS:
            relevant.append(e)
            continue
        if "SEMI" in e.affected_tags and sid in SEMI_STOCKS:
            relevant.append(e)
            continue
        # 全市場事件 (MACRO) 對所有股票都有影響但較弱
        if "MACRO" in e.affected_tags:
            relevant.append(e)
    return relevant


def warnings_for_stock(stock_id: str, within_days: int = 14) -> list[str]:
    """為個股回傳事件警示文字"""
    events = relevant_events_for_stock(stock_id, within_days)
    today = datetime.now().date()
    out = []
    for e in events[:3]:  # 最多 3 則避免訊息過長
        d = e.days_from(today)
        when = "今天" if d == 0 else f"明天" if d == 1 else f"{d} 天後"
        emoji = "🚨" if d <= 2 and e.severity == "high" else "📅" if d <= 7 else "🗓️"
        out.append(f"{emoji} {when}：{e.name}（{e.impact}）")
    return out


def market_event_summary(within_days: int = 7) -> str:
    """大盤層級的近期事件摘要（用於 Telegram 推播）"""
    events = [e for e in get_upcoming_events(within_days)
              if "MACRO" in e.affected_tags or e.severity == "high"]
    if not events:
        return ""
    today = datetime.now().date()
    lines = ["📅 *近期重要事件*"]
    for e in events[:5]:
        d = e.days_from(today)
        when = "今天" if d == 0 else f"明天" if d == 1 else f"{d} 天後"
        lines.append(f"• {when}：{e.name}（{e.impact}）")
    return "\n".join(lines)
