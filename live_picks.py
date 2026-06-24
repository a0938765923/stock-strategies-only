"""即時盤勢推薦 — 抓現在實際漲跌 + 大盤 Regime → 給「現在該買什麼」

特色：
  ✅ twstock 直連 TWSE，1~2 分鐘延遲（vs Yahoo 15 分鐘）
  ✅ 1 個 API 請求抓 40 檔（不會觸發 rate limit）
  ✅ 按族群排名 + 當下漲幅 + 大盤 Regime 加權
  ✅ < 30 秒跑完

執行: uv run python live_picks.py
"""

from __future__ import annotations

import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.regime import get_market_regime
from intraday_monitor import twstock_batch_prices


# ============================================================
# 2026 熱門題材股池（涵蓋 8 大族群、40 檔）
# ============================================================
UNIVERSE = {
    "AI Server": [
        ("6669", "緯穎"), ("2382", "廣達"), ("2376", "技嘉"),
        ("3231", "緯創"), ("2357", "華碩"),
    ],
    "AI 散熱": [
        ("3017", "奇鋐"), ("3653", "健策"), ("8210", "勤誠"),
        ("6230", "尼得科超眾"),
    ],
    "ASIC / IP": [
        ("3661", "世芯-KY"), ("3035", "智原"), ("5274", "信驊"),
        ("2379", "瑞昱"),
    ],
    "AI 電源": [
        ("2308", "台達電"), ("3450", "聯鈞"),
    ],
    "AI 基建 / 重電": [
        ("1519", "華城"), ("1503", "士電"), ("1504", "東元"),
    ],
    "機器人 / 自動化": [
        ("2049", "上銀"), ("2360", "致茂"),
    ],
    "半導體": [
        ("2330", "台積電"), ("2454", "聯發科"), ("3037", "欣興"),
        ("2449", "京元電子"),
    ],
    "金融": [
        ("2885", "元大金"), ("2891", "中信金"), ("2890", "永豐金"),
        ("2884", "玉山金"), ("2881", "富邦金"),
    ],
    "防守 / ETF": [
        ("4904", "遠傳"), ("2412", "中華電"),
        ("0050", "元大台灣50"), ("0052", "富邦科技"),
        ("00940", "元大價值高息"), ("00929", "復華科技優息"),
        ("00878", "國泰永續高股息"),
    ],
}


def collect_universe() -> list[tuple[str, str, str]]:
    """攤平整個宇宙：[(stock_id, name, sector), ...]"""
    flat = []
    for sector, stocks in UNIVERSE.items():
        for sid, name in stocks:
            flat.append((sid, name, sector))
    return flat


def main():
    print("=" * 70)
    print(f"📊 即時盤勢推薦 · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)
    print()

    # 1. 抓大盤 Regime
    print("🔍 偵測大盤 Regime ...")
    regime = get_market_regime()
    print(f"  {regime['note']}")
    print()

    # 2. 一次批次抓所有股票（twstock 1 個請求）
    flat = collect_universe()
    print(f"📡 抓 {len(flat)} 檔即時報價（1 個 API 請求）...")
    ids = [s[0] for s in flat]
    prices = twstock_batch_prices(ids)
    print(f"  → {len(prices)}/{len(ids)} 檔成功")
    print()

    # 3. 整理每檔資料
    rows = []
    for sid, name, sector in flat:
        p = prices.get(sid)
        if not p:
            continue
        rows.append({
            "stock_id": sid, "name": name, "sector": sector,
            "current": p["current"],
            "open": p["previous_close"],  # 注意：這裡 twstock 是「今日開盤」
            "day_pct": p["day_change_pct"] * 100,
        })

    if not rows:
        print("⚠️ 沒抓到任何資料")
        return

    # 4. 族群排行：每族群平均漲幅
    sector_perf: dict[str, list[float]] = {}
    for r in rows:
        sector_perf.setdefault(r["sector"], []).append(r["day_pct"])
    sector_rank = sorted(
        [(s, sum(p)/len(p), len(p)) for s, p in sector_perf.items()],
        key=lambda x: -x[1],
    )

    print("=" * 70)
    print("🔥 族群漲幅排行（今天表現）")
    print("=" * 70)
    for i, (sector, avg, n) in enumerate(sector_rank, 1):
        emoji = "🔥" if avg > 1.5 else "📈" if avg > 0 else "📉" if avg > -1.5 else "💥"
        print(f"  {i}. {emoji} {sector:<18}{avg:+6.2f}% ({n} 檔平均)")
    print()

    # 5. 個股強弱榜
    rows_ranked = sorted(rows, key=lambda x: -x["day_pct"])
    print("=" * 70)
    print(f"⭐ 個股強弱榜（前 10 強 · 共 {len(rows)} 檔）")
    print("=" * 70)
    print(f"  {'股號':<8}{'名稱':<12}{'族群':<18}{'現價':>10}{'今日':>10}")
    print("  " + "-" * 60)
    for r in rows_ranked[:10]:
        arrow = "🚀" if r["day_pct"] > 3 else "🟢" if r["day_pct"] > 0 else "🔴"
        print(f"  {r['stock_id']:<8}{r['name']:<12}{r['sector']:<18}"
              f"{r['current']:>9.2f}{r['day_pct']:>+8.2f}% {arrow}")
    print()

    print(f"😱 個股弱勢榜（後 5 弱）")
    print(f"  {'股號':<8}{'名稱':<12}{'族群':<18}{'現價':>10}{'今日':>10}")
    print("  " + "-" * 60)
    for r in rows_ranked[-5:]:
        print(f"  {r['stock_id']:<8}{r['name']:<12}{r['sector']:<18}"
              f"{r['current']:>9.2f}{r['day_pct']:>+8.2f}% 🔴")
    print()

    # 6. 我的推薦
    print("=" * 70)
    print("🎯 我的推薦（基於大盤 Regime + 族群強度 + 個股表現）")
    print("=" * 70)

    r_type = regime.get("regime", "SIDEWAYS")
    r_score = regime.get("score", 50)

    if r_type == "BULL":
        print(f"📈 大盤 {r_type} ({r_score:.0f}/100) → 跟強勢族群 + 強勢個股")
        # 取前 3 強族群裡的最強個股
        top_sectors = [s for s, _, _ in sector_rank[:3]]
        picks = [r for r in rows_ranked if r["sector"] in top_sectors][:7]
    elif r_type == "BEAR":
        print(f"📉 大盤 {r_type} ({r_score:.0f}/100) → 防守為主、ETF + 金融")
        defensive = {"防守 / ETF", "金融"}
        picks = [r for r in rows_ranked if r["sector"] in defensive][:5]
    else:  # SIDEWAYS
        print(f"🦘 大盤 {r_type} ({r_score:.0f}/100) → 只挑最強的 3 檔、嚴設停損")
        picks = rows_ranked[:3]

    print()
    print(f"  推薦進場觀察名單（{len(picks)} 檔）：")
    for i, r in enumerate(picks, 1):
        print(f"  {i}. {r['stock_id']} {r['name']} ({r['sector']}) "
              f"@ {r['current']:.2f} 今 {r['day_pct']:+.2f}%")

    # 警示
    if r_type == "BEAR":
        print()
        print("  ⚠️ 大盤偏空，建議減碼 50%、嚴設停損 -5%")
    elif r_score < 50:
        print()
        print(f"  ⚠️ Regime 分數偏低（{r_score:.0f}），保守為佳")

    print()
    print("─" * 70)
    print("💡 操作建議：")
    print("  • 進場前確認個股是否站上 20MA")
    print("  • 用 ATR 設停損（建議 2~3 倍 ATR）")
    print("  • 強勢族群第一波拉漲時跟，回測再進更安全")
    print("  • 弱勢個股逆勢勿買、即使便宜也別接刀")


if __name__ == "__main__":
    main()
