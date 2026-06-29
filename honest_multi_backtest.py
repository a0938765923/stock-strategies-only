"""
誠實多策略對戰 — V21 vs V7 SEPA vs V17 多因子 vs Buy&Hold
==========================================================
規則統一、公平對比：
  • 真實 FinMind 日線
  • 隔日開盤進場/出場（無前視）
  • 全成本：手續費 0.1425%×2 + 證交稅 0.3% + 滑價 0.1%×2
  • 每日 mark-to-market → 正確年化 Sharpe
  • 長多單一持倉 100% 資金（與 B&H 公平比較）
  • 樣本內(前66%) / 樣本外(後34%) walk-forward
每個策略用「自然進出場」：
  • V21：爆量突破進場，+10%/-5%/持有5日出場
  • V7 SEPA：站上 EMA50>EMA200 且 EMA50 上彎進場，跌破 EMA50 出場（趨勢跟隨）
  • V17 多因子：均線多排+RSI+MACD 達標進場，分數轉弱出場
"""
from __future__ import annotations
import os, sys, time, math
import numpy as np
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

COMMISSION = 0.001425
TAX        = 0.003
SLIPPAGE   = 0.001
RISK_FREE  = 0.015

WATCHLIST = [
    ("2330", "台積電"), ("2454", "聯發科"), ("2317", "鴻海"),
    ("2308", "台達電"), ("2382", "廣達"),  ("3034", "聯詠"),
    ("2412", "中華電"), ("2881", "富邦金"), ("2882", "國泰金"),
    ("2603", "長榮"),  ("3008", "大立光"), ("2409", "友達"),
    ("3231", "緯創"),  ("2357", "華碩"),  ("2379", "瑞昱"),
]


# ── 各策略：回傳 entry(bool series) 與 exit 判定函式 ──
def indicators(df):
    d = {}
    d["vol_ma"]   = df["volume"].rolling(20).mean()
    d["hi_52w"]   = df["high"].shift(1).rolling(260).max()
    d["vol_prev"] = df["volume"].shift(1)
    d["ema50"]    = df["close"].ewm(span=50, adjust=False).mean()
    d["ema200"]   = df["close"].ewm(span=200, adjust=False).mean()
    d["sma20"]    = df["close"].rolling(20).mean()
    d["sma50"]    = df["close"].rolling(50).mean()
    d["sma200"]   = df["close"].rolling(200).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    d["rsi"] = (100 - 100 / (1 + rs)).fillna(50)
    ema_f = df["close"].ewm(span=12, adjust=False).mean()
    ema_s = df["close"].ewm(span=26, adjust=False).mean()
    macd = ema_f - ema_s
    d["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
    return d


def v21_entry(df, d):
    move = (df["close"] - df["open"]) / df["open"] * 100
    return ((df["volume"] > d["vol_ma"] * 1.5) &
            (df["volume"] > d["vol_prev"] * 1.5) &
            (df["close"] > d["hi_52w"]) & (move >= 2.0)).fillna(False)


def v7_entry(df, d):
    return ((df["close"] > d["ema50"]) & (d["ema50"] > d["ema200"]) &
            (d["ema50"] > d["ema50"].shift(1))).fillna(False)


def v17_score(df, d):
    s = ((df["close"] > d["sma20"]).astype(int) +
         (d["sma20"] > d["sma50"]).astype(int) +
         (d["sma50"] > d["sma200"]).astype(int) +
         ((d["rsi"] >= 45) & (d["rsi"] <= 70)).astype(int) +
         (d["macd_hist"] > 0).astype(int))
    return s


STRATS = {
    "V21": dict(
        entry=lambda df, d: v21_entry(df, d),
        # 出場：達標/停損/持有滿5日
        exit=lambda df, d, i, entry_px, bars: (
            (df["close"].iat[i]-entry_px)/entry_px*100 >= 10 or
            (df["close"].iat[i]-entry_px)/entry_px*100 <= -5 or bars >= 5),
    ),
    "V7_SEPA": dict(
        entry=lambda df, d: v7_entry(df, d),
        # 出場：跌破 EMA50（趨勢轉弱）
        exit=lambda df, d, i, entry_px, bars: df["close"].iat[i] < d["ema50"].iat[i],
    ),
    "V17_多因子": dict(
        entry=lambda df, d: (v17_score(df, d) >= 4).fillna(False),
        # 出場：分數掉到 2 以下
        exit=lambda df, d, i, entry_px, bars: v17_score(df, d).iat[i] <= 2,
    ),
}


def run_strategy(df, d, entry_sig, exit_fn, lo, hi):
    """每日 mark-to-market，回傳 daily equity series(index=date) 與交易報酬列表"""
    o = df["open"].values
    c = df["close"].values
    dates = pd.to_datetime(df["date"]).values
    eq = 1.0
    pos = False
    entry_px = 0.0
    bars = 0
    eq_list, trades = [], []
    for i in range(lo, hi):
        if pos:
            # 先算今日 mark-to-market（昨收→今收）
            eq *= c[i] / c[i-1]
            bars += 1
            if exit_fn(df, d, i, entry_px, bars):
                # 隔日開盤出場，扣成本（相對今收的調整）
                exit_px = o[min(i+1, len(o)-1)] * (1 - SLIPPAGE)
                adj = exit_px / c[i] * (1 - COMMISSION - TAX)
                eq *= adj
                trades.append((exit_px*(1-COMMISSION-TAX) - entry_px) / entry_px * 100)
                pos = False
        else:
            if entry_sig.iat[i-1]:
                entry_px = o[i] * (1 + SLIPPAGE) * (1 + COMMISSION)
                # 進場日：開盤買，當日 mark 到收盤
                eq *= c[i] / entry_px
                pos = True
                bars = 0
        eq_list.append((dates[i], eq))
    return pd.DataFrame(eq_list, columns=["date","eq"]).set_index("date")["eq"], trades


def metrics(eq, trades, years):
    if len(eq) < 2 or years <= 0:
        return dict(cagr=0, sharpe=0, mdd=0, wr=0, n=0)
    final = eq.iloc[-1]
    cagr = (final ** (1/years) - 1) * 100
    daily = eq.pct_change().dropna()
    sharpe = ((daily.mean() - RISK_FREE/252) / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0
    mdd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    wr = (len([t for t in trades if t>0]) / len(trades) * 100) if trades else 0
    return dict(cagr=cagr, sharpe=sharpe, mdd=mdd, wr=wr, n=len(trades))


def bh_metrics(df, lo, hi, years):
    seg = df["close"].iloc[lo:hi].reset_index(drop=True)
    eq = (seg / seg.iloc[0])
    eq.index = pd.to_datetime(df["date"].iloc[lo:hi].values)
    return metrics(eq, [], years)


def main():
    if not os.environ.get("FINMIND_TOKEN"):
        print("❌ 缺少 FINMIND_TOKEN"); sys.exit(1)

    print("="*86)
    print("誠實多策略對戰（真實股價 + 全成本 + Walk-forward + 每日Sharpe）")
    print("="*86)

    acc = {name: {"is": [], "oos": []} for name in list(STRATS) + ["BuyHold"]}

    for sid, name in WATCHLIST:
        try:
            df = get_price_history(sid, 3)
            if len(df) < 340:
                continue
            df = df.reset_index(drop=True)
            d = indicators(df)
            n = len(df)
            warm = 265
            split = warm + int((n - warm) * 0.66)
            yr_is  = (pd.to_datetime(df["date"].iat[split]) - pd.to_datetime(df["date"].iat[warm])).days/365.25
            yr_oos = (pd.to_datetime(df["date"].iat[n-1]) - pd.to_datetime(df["date"].iat[split])).days/365.25

            for sname, cfg in STRATS.items():
                ent = cfg["entry"](df, d)
                eq_is,  tr_is  = run_strategy(df, d, ent, cfg["exit"], warm, split)
                eq_oos, tr_oos = run_strategy(df, d, ent, cfg["exit"], split, n)
                acc[sname]["is"].append(metrics(eq_is, tr_is, yr_is))
                acc[sname]["oos"].append(metrics(eq_oos, tr_oos, yr_oos))
            acc["BuyHold"]["is"].append(bh_metrics(df, warm, split, yr_is))
            acc["BuyHold"]["oos"].append(bh_metrics(df, split, n, yr_oos))
            print(f"  ✓ {sid} {name}")
            time.sleep(0.12)
        except Exception as e:
            print(f"  ✗ {sid} {name}: {str(e)[:40]}")

    def avg(lst, k):
        v = [m[k] for m in lst]
        return np.mean(v) if v else 0
    def total_n(lst):
        return sum(m["n"] for m in lst)

    print()
    print("="*86)
    print(f"{'策略':<12}{'樣本內CAGR':>12}{'樣本外CAGR':>12}{'樣本外Sharpe':>14}{'樣本外MDD':>12}{'樣本外勝率':>12}{'交易數':>8}")
    print("-"*86)
    for name in ["BuyHold"] + list(STRATS):
        ai, ao = acc[name]["is"], acc[name]["oos"]
        line = (f"{name:<12}{avg(ai,'cagr'):>11.2f}%{avg(ao,'cagr'):>11.2f}%"
                f"{avg(ao,'sharpe'):>14.2f}{avg(ao,'mdd'):>11.2f}%")
        if name == "BuyHold":
            line += f"{'—':>12}{'—':>8}"
        else:
            line += f"{avg(ao,'wr'):>11.1f}%{total_n(ao):>8}"
        print(line)
    print("="*86)

    bh_oos = avg(acc["BuyHold"]["oos"], "cagr")
    print()
    print("【判讀】（樣本外 = 沒被最佳化過的真實考驗）")
    winner = None
    for name in STRATS:
        c = avg(acc[name]["oos"], "cagr")
        mark = "✅ 贏" if c > bh_oos else "🔴 輸"
        print(f"  • {name:<10} 樣本外 {c:+.2f}%  vs  B&H {bh_oos:+.2f}%  → {mark}")
        if c > bh_oos and (winner is None or c > avg(acc[winner]["oos"],"cagr")):
            winner = name
    print()
    if winner:
        print(f"  🏆 唯一打贏 B&H 的是 {winner}，但仍需注意樣本數與穩定度")
    else:
        print(f"  ⚠️  沒有任何擇時策略打贏單純 Buy&Hold（+{bh_oos:.1f}%）")
        print(f"  → 數據結論：多頭市場，買進持有最強。應轉向『持有 + 風控提醒』")


if __name__ == "__main__":
    main()
