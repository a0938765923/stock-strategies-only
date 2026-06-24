"""FinLab 補強指標 — 借券餘額、月營收強度（FinMind 沒有的）

借券餘額激增意義：
  機構/大戶向券商借股票準備做空
  → 反向指標：餘額快速增加 = 法人看空
  → 結合股價漲跌：股價漲但借券大增 → 高檔有人做空 → 頂部訊號

月營收強度（FinLab 已算好 YoY、MoM）：
  比我們從原始營收自算更精準（公告日對齊）

回傳 warnings 整合進 risk_aggregator。
失敗會 gracefully 回空 list，不中斷主流程。
"""

from __future__ import annotations

import os
import sys


_FINLAB_LOGGED_IN = False


def _ensure_login() -> bool:
    """惰性登入。沒有 token 或失敗回 False，呼叫端跳過 FinLab 邏輯。"""
    global _FINLAB_LOGGED_IN
    if _FINLAB_LOGGED_IN:
        return True
    token = os.environ.get("FINLAB_TOKEN", "").strip()
    if not token:
        return False
    try:
        import finlab
        finlab.login(api_token=token)
        _FINLAB_LOGGED_IN = True
        return True
    except Exception as e:
        print(f"[finlab_extra] 登入失敗: {str(e)[:80]}", file=sys.stderr)
        return False


def detect_finlab_warnings(stock_id: str) -> list[str]:
    """用 FinLab 獨家資料偵測警示。沒 token 或失敗回空 list。"""
    if not _ensure_login():
        return []

    warnings: list[str] = []
    sid = str(stock_id).strip()

    try:
        from finlab import data
    except ImportError:
        return []

    # === 1. 借券餘額激增警示 ===
    try:
        sl = data.get("security_lending:借券餘額")
        if sid in sl.columns and len(sl) >= 25:
            s = sl[sid].dropna().tail(25)
            if len(s) >= 20:
                cur = float(s.iloc[-1])
                base = float(s.iloc[-20])
                if base > 0:
                    growth = (cur - base) / base
                    if growth > 0.30 and cur > 1_000_000:
                        warnings.append(
                            f"🚨 借券餘額 20 日激增 {growth*100:+.0f}%（機構準備做空，頂部訊號）"
                        )
                    elif growth > 0.15 and cur > 1_000_000:
                        warnings.append(
                            f"⚠️ 借券餘額 20 日增 {growth*100:+.0f}%（留意做空動向）"
                        )
    except Exception:
        pass

    # === 2. 月營收 YoY 強度（FinLab 比 FinMind 算更準） ===
    try:
        yoy = data.get("monthly_revenue:去年同月增減(%)")
        if sid in yoy.columns and len(yoy) >= 3:
            recent = yoy[sid].dropna().tail(3)
            if len(recent) >= 2:
                last_yoy = float(recent.iloc[-1])
                if last_yoy < -20:
                    warnings.append(
                        f"📉 最近月營收 YoY {last_yoy:+.1f}%（嚴重衰退，基本面惡化）"
                    )
                elif last_yoy < -10 and float(recent.iloc[-2]) < 0:
                    warnings.append(
                        f"⚠️ 連 2 個月 YoY 為負（最近 {last_yoy:+.1f}%）"
                    )
    except Exception:
        pass

    # === 3. EPS 警示（季報週期慢，當輔助） ===
    try:
        eps = data.get("financial_statement:每股盈餘")
        if sid in eps.columns and len(eps) >= 3:
            recent = eps[sid].dropna().tail(3)
            if len(recent) >= 3:
                last_eps = float(recent.iloc[-1])
                prev_eps = float(recent.iloc[-2])
                if last_eps < 0:
                    warnings.append(
                        f"🔴 最新季 EPS 為負 {last_eps:.2f}（虧損）"
                    )
                elif prev_eps > 0 and last_eps / prev_eps < 0.5 and prev_eps > 1:
                    warnings.append(
                        f"⚠️ EPS 季減半：{prev_eps:.2f} → {last_eps:.2f}（獲利衰退）"
                    )
    except Exception:
        pass

    return warnings
