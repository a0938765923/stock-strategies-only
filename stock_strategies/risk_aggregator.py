"""風險聚合器 — 整合 4 大警示模組（籌碼/技術/事件/新聞）

提供統一接口，給 main.py / live_picks.py 一鍵呼叫。
每檔股票最多回 5 條警示（避免訊息過長）。
"""

from __future__ import annotations

from .chips_risk import detect_chip_risks
from .technical_warning import detect_technical_warnings
from .events_calendar import warnings_for_stock
from .news_sentiment import get_news_sentiment
from .finlab_extra import detect_finlab_warnings


def aggregate_warnings(stock_id: str, max_total: int = 5,
                       include_news: bool = True) -> dict:
    """為單檔股票收集 4 大類警示。

    回 dict {
       chips, technical, events, news (list of str),
       sentiment_score, sentiment_label,
       all_warnings (合併後 max_total 條),
       risk_level (LOW / MEDIUM / HIGH)
    }
    """
    chips = detect_chip_risks(stock_id)
    technical = detect_technical_warnings(stock_id)
    events = warnings_for_stock(stock_id)
    finlab = detect_finlab_warnings(stock_id)

    news_data = {"warnings": [], "sentiment": "⚪ 無資料", "score": 0}
    if include_news:
        try:
            news_data = get_news_sentiment(stock_id)
        except Exception:
            pass
    news = news_data.get("warnings", [])

    # 合併（順序：技術 > 籌碼 > FinLab 借券 > 新聞 > 事件）
    all_warnings: list[str] = []
    for src in (technical, chips, finlab, news, events):
        for w in src:
            if w not in all_warnings:
                all_warnings.append(w)
            if len(all_warnings) >= max_total:
                break
        if len(all_warnings) >= max_total:
            break

    # 風險等級
    n_chips = len(chips)
    n_tech = len(technical)
    n_news = len(news)
    score = news_data.get("score", 0)
    if n_chips + n_tech >= 3 or score <= -3:
        risk_level = "HIGH"
    elif n_chips + n_tech + n_news >= 2 or score <= -1:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "chips": chips,
        "technical": technical,
        "events": events,
        "news": news,
        "finlab": finlab,
        "sentiment_label": news_data.get("sentiment", "⚪"),
        "sentiment_score": news_data.get("score", 0),
        "all_warnings": all_warnings,
        "risk_level": risk_level,
    }
