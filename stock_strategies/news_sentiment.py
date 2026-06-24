"""新聞情感分析（基礎版，不需 LLM）

抓 Yahoo 股市新聞 RSS + 關鍵字情感判斷。免費、無延遲、無 API key。

情感判斷邏輯：
  正面關鍵字：噴漲、上看、外資加碼、法人連買、營收創高、訂單滿、漲停
  負面關鍵字：跌停、利空、賣超、減產、衰退、調降、警示、跳水
  頂部訊號：上看、目標價、瘋搶、撈底、見高、上看 XX

  分數 = (正面數 × 1) - (負面數 × 1.2) - (頂部訊號 × 1.5)

回 dict {score, sentiment, headlines, top_signals}
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

import requests


POSITIVE_KW = [
    "噴漲", "外資加碼", "法人連買", "營收創高", "訂單滿", "強勢", "突破",
    "利多", "看漲", "領漲", "領頭", "超預期", "獲利", "上修", "強力",
]
NEGATIVE_KW = [
    "跌停", "利空", "賣超", "減產", "衰退", "調降", "警示", "跳水",
    "崩跌", "重挫", "倒貨", "下修", "失守", "走弱", "賣壓", "出貨", "減持",
]
# 頂部訊號（小心型）
TOP_SIGNAL_KW = [
    "上看", "目標價", "瘋搶", "撈底", "見高", "天價",
    "新天價", "歷史新高", "創高",
]


def fetch_yahoo_news_headlines(stock_id: str, limit: int = 15) -> list[dict]:
    """抓 Yahoo 股市某檔個股的新聞標題。

    用 Yahoo 股市 RSS API（公開、不需登入）。失敗回空 list。
    """
    # Yahoo TW 股市新聞 RSS
    url = f"https://tw.stock.yahoo.com/rss?s={quote(stock_id)}.TW"
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        text = r.text
    except Exception:
        return []

    # 簡易解析（不依賴 lxml/BeautifulSoup）
    titles = re.findall(r"<title>(.*?)</title>", text, re.DOTALL)
    pub_dates = re.findall(r"<pubDate>(.*?)</pubDate>", text, re.DOTALL)

    # 去掉 RSS 自己的 title（通常第 1 個）
    items = []
    for t, p in zip(titles[1:limit+1], pub_dates[:limit]):
        items.append({"title": t.strip(), "pub_date": p.strip()})
    return items


def analyze_sentiment(headlines: list[dict]) -> dict:
    """對標題清單做關鍵字情感計算"""
    pos = neg = top = 0
    matched_top_signals: list[str] = []
    matched_negatives: list[str] = []
    for h in headlines:
        title = h.get("title", "")
        for kw in POSITIVE_KW:
            if kw in title:
                pos += 1
                break
        for kw in NEGATIVE_KW:
            if kw in title:
                neg += 1
                matched_negatives.append(f"{kw}: {title[:30]}")
                break
        for kw in TOP_SIGNAL_KW:
            if kw in title:
                top += 1
                matched_top_signals.append(f"{kw}: {title[:30]}")
                break

    score = pos - neg * 1.2 - top * 1.5
    if score >= 2:
        sentiment = "🟢 偏多"
    elif score <= -2:
        sentiment = "🔴 偏空"
    elif top >= 2:
        sentiment = "🚨 頂部訊號"
    else:
        sentiment = "⚪ 中性"

    return {
        "score": round(score, 1),
        "sentiment": sentiment,
        "positive_count": pos,
        "negative_count": neg,
        "top_signal_count": top,
        "matched_top_signals": matched_top_signals[:3],
        "matched_negatives": matched_negatives[:3],
        "n_headlines": len(headlines),
    }


def get_news_sentiment(stock_id: str) -> dict:
    """主入口：抓新聞 + 分析情感。"""
    headlines = fetch_yahoo_news_headlines(stock_id)
    if not headlines:
        return {
            "sentiment": "⚪ 無資料",
            "score": 0,
            "n_headlines": 0,
            "warnings": [],
        }
    result = analyze_sentiment(headlines)

    # 整理成易讀警示
    warnings = []
    if result["top_signal_count"] >= 2:
        warnings.append(
            f"🚨 頂部訊號：{result['top_signal_count']} 則新聞出現「上看/目標價/天價」字眼，散戶情緒過熱"
        )
    if result["negative_count"] >= 3:
        warnings.append(
            f"📉 利空集中：{result['negative_count']} 則負面新聞（跌停/賣超/減產/警示等）"
        )
    if result["score"] <= -3:
        warnings.append(
            f"🔴 新聞情感極差：分數 {result['score']:.1f}，避免進場"
        )
    elif result["score"] >= 3:
        warnings.append(
            f"🟢 新聞情感極佳：分數 {result['score']:.1f}，但須留意是否過熱"
        )

    result["warnings"] = warnings
    return result
