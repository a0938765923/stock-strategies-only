"""市場掃描器 — 每週日 23:00 跑

掃描約 150 檔熱門股（你 Watchlist 以外），用同樣策略分析後推薦 Top 10。
讓你不會錯過 Watchlist 沒涵蓋的新機會，每週決定要不要把它們加進來。

執行: uv run python market_scanner.py
"""

import os
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.sheet import read_watchlist, get_gsheet
from stock_strategies.evaluate import evaluate
from stock_strategies.notify import send_telegram
import gspread


REQUIRED_ENV = [
    "FINMIND_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
]

# ============================================================
# 掃描宇宙 — ~150 檔熱門股，涵蓋 AI、半導體、金融、傳產、ETF
# 想新增/刪除直接改這個 list
# ============================================================
SCAN_UNIVERSE: list[tuple[str, str, str]] = [
    # AI / Server / GPU 概念股
    ("2330", "台積電", "半導體"),
    ("2454", "聯發科", "半導體"),
    ("2379", "瑞昱", "半導體"),
    ("3034", "聯詠", "半導體"),
    ("3037", "欣興", "ABF載板"),
    ("3661", "世芯-KY", "ASIC"),
    ("3035", "智原", "ASIC"),
    ("5347", "世界", "晶圓代工"),
    ("2449", "京元電子", "封測"),
    ("6285", "啟碁", "網通"),
    ("5274", "信驊", "BMC"),
    ("2308", "台達電", "電源"),
    ("2382", "廣達", "AI Server"),
    ("2376", "技嘉", "AI Server"),
    ("2356", "英業達", "AI Server"),
    ("2357", "華碩", "PC"),
    ("3231", "緯創", "AI Server"),
    ("6669", "緯穎", "AI Server"),
    ("3017", "奇鋐", "AI 散熱"),
    ("3653", "健策", "AI 散熱"),
    ("8210", "勤誠", "機殼"),
    ("2317", "鴻海", "AI Server"),
    ("2354", "鴻準", "機殼"),
    ("2474", "可成", "機殼"),
    ("6230", "尼得科超眾", "散熱"),
    ("3653", "健策", "散熱"),
    ("2408", "南亞科", "DRAM"),
    ("3105", "穩懋", "射頻"),
    ("4961", "天鈺", "驅動IC"),
    ("3596", "智易", "網通"),
    ("3702", "大聯大", "通路"),
    ("3036", "文曄", "通路"),
    ("2347", "聯強", "通路"),
    ("8046", "南電", "ABF載板"),
    ("3293", "鈊象", "遊戲"),
    ("8358", "金居", "銅箔"),
    ("6443", "元晶", "太陽能"),

    # 金融
    ("2881", "富邦金", "金融"),
    ("2882", "國泰金", "金融"),
    ("2884", "玉山金", "金融"),
    ("2885", "元大金", "金融"),
    ("2886", "兆豐金", "金融"),
    ("2887", "台新金", "金融"),
    ("2890", "永豐金", "金融"),
    ("2891", "中信金", "金融"),
    ("2892", "第一金", "金融"),
    ("2880", "華南金", "金融"),
    ("5876", "上海商銀", "金融"),
    ("5880", "合庫金", "金融"),
    ("2823", "中壽", "金融"),

    # 傳產 / 內需
    ("1101", "台泥", "水泥"),
    ("1102", "亞泥", "水泥"),
    ("1216", "統一", "食品"),
    ("1303", "南亞", "塑膠"),
    ("1301", "台塑", "塑膠"),
    ("1326", "台化", "塑膠"),
    ("1402", "遠東新", "紡織"),
    ("2105", "正新", "輪胎"),
    ("2207", "和泰車", "汽車"),
    ("2412", "中華電", "電信"),
    ("3045", "台灣大", "電信"),
    ("4904", "遠傳", "電信"),
    ("2603", "長榮", "航運"),
    ("2615", "萬海", "航運"),
    ("2609", "陽明", "航運"),
    ("9904", "寶成", "鞋業"),
    ("9921", "巨大", "自行車"),
    ("9914", "美利達", "自行車"),
    ("1227", "佳格", "食品"),
    ("9910", "豐泰", "鞋業"),
    ("1232", "大統益", "食品"),

    # 鋼鐵 / 機械
    ("2002", "中鋼", "鋼鐵"),
    ("1605", "華新", "電線"),
    ("2049", "上銀", "工具機"),
    ("1504", "東元", "重電"),
    ("1503", "士電", "重電"),
    ("1519", "華城", "重電"),

    # 生技
    ("4174", "浩鼎", "生技"),
    ("6446", "藥華藥", "生技"),
    ("4736", "泰博", "生技"),
    ("6492", "生華科", "生技"),

    # 觀光 / 餐飲
    ("2731", "雄獅", "觀光"),
    ("2723", "美食-KY", "餐飲"),
    ("9933", "中鼎", "工程"),

    # 高股息 / 防守
    ("2912", "統一超", "通路"),
    ("9939", "宏全", "包材"),
    ("1714", "和桐", "化工"),

    # ETF（你已有 0050 0056，加幾檔熱門高股息 ETF）
    ("00878", "國泰永續高股息", "ETF"),
    ("00919", "群益台灣精選高息", "ETF"),
    ("00929", "復華台灣科技優息", "ETF"),
    ("00939", "統一台灣高息動能", "ETF"),
    ("00940", "元大台灣價值高息", "ETF"),
    ("00713", "元大台灣高息低波", "ETF"),
    ("0052", "富邦科技", "ETF"),
    ("00692", "富邦公司治理", "ETF"),
    ("00881", "國泰台灣 5G+", "ETF"),
    ("00891", "中信關鍵半導體", "ETF"),

    # 個股補強
    ("4763", "材料-KY", "材料"),
    ("6664", "群聯", "記憶體"),
    ("2363", "矽統", "半導體"),
    ("3406", "玉晶光", "光電"),
    ("3008", "大立光", "光電"),
    ("2439", "美律", "電聲"),
    ("2360", "致茂", "測試設備"),
    ("3042", "晶技", "石英"),
    ("3211", "順達", "電池"),
    ("4760", "勤凱", "化工"),
    ("4977", "眾達-KY", "光通訊"),
    ("4763", "材料-KY", "光學材料"),
    ("3450", "聯鈞", "光纖"),
    ("6679", "鈺太", "晶片"),
    ("4938", "和碩", "EMS"),
    ("4906", "正文", "網通"),
    ("3380", "明泰", "網通"),
    ("3036", "文曄", "通路"),
    ("2884", "玉山金", "金融"),  # 重複防呆
]


def _dedupe_universe(universe: list[tuple]) -> list[tuple]:
    """去重（同 stock_id 只保留第一筆）"""
    seen = set()
    out = []
    for sid, name, cat in universe:
        if sid in seen:
            continue
        seen.add(sid)
        out.append((sid, name, cat))
    return out


def _write_discoveries(records: list[dict]):
    """把掃描結果寫進 Sheet 的 Discoveries 分頁（不存在就建）"""
    sh = get_gsheet()
    headers = [
        "scan_date", "rank", "stock_id", "name", "category",
        "action", "signal_score", "entry_price", "stop_loss_price",
        "target_price", "rr_ratio", "winrate", "samples",
    ]
    try:
        ws = sh.worksheet("Discoveries")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Discoveries", rows=2000, cols=20)
        ws.append_row(headers)

    rows = []
    today = datetime.now().strftime("%Y-%m-%d")
    for rank, r in enumerate(records, 1):
        c = r.get("components", {})
        rows.append([
            today, rank,
            r.get("stock_id", ""), r.get("name", ""), r.get("_category", ""),
            r.get("action", ""), r.get("signal_score", ""),
            r.get("entry_price", ""), r.get("stop_loss_price", ""),
            r.get("target_price", ""), r.get("risk_reward_ratio", ""),
            c.get("backtest_winrate", ""), c.get("backtest_samples", ""),
        ])
    if rows:
        ws.append_rows(rows)


def _format_message(top: list[dict], total_scanned: int, watchlist_size: int) -> str:
    today = datetime.now()
    lines = [
        f"🔍 *本週新機會掃描* {today.strftime('%Y/%m/%d')}",
        f"_掃描 {total_scanned} 檔 · 排除你 Watchlist {watchlist_size} 檔 · Top {len(top)}_",
        "",
    ]
    if not top:
        lines.append("📋 本週**無**新標的達到 BUY/WATCH 門檻")
        lines.append("→ 維持現有 Watchlist 即可")
        return "\n".join(lines)

    buys = [r for r in top if str(r.get("action", "")).upper() == "BUY"]
    watches = [r for r in top if str(r.get("action", "")).upper() == "WATCH"]

    if buys:
        lines.append(f"🟢 *BUY 候選* ({len(buys)} 檔)")
        for r in buys:
            c = r.get("components", {})
            wr = c.get("backtest_winrate", "?")
            lines.append(
                f"• *{r['stock_id']} {r['name']}* ({r.get('_category', '')}) {r.get('signal_score', '')}分"
            )
            lines.append(
                f"  進場 {r.get('entry_price', '—')} / 停損 {r.get('stop_loss_price', '—')} / "
                f"目標 {r.get('target_price', '—')} (勝率 {wr}%)"
            )
        lines.append("")

    if watches:
        lines.append(f"🟡 *WATCH 候選* ({len(watches)} 檔)")
        for r in watches:
            lines.append(
                f"• {r['stock_id']} {r['name']} ({r.get('_category', '')}) {r.get('signal_score', '')}分"
            )
        lines.append("")

    lines.append("━━━━━━━━━━")
    lines.append("📝 *怎麼用*")
    lines.append("• 喜歡的標的 → 加進 Watchlist（Sheet 加一列）")
    lines.append("• 每天 14:30 系統就會分析這檔了")
    lines.append("• 完整資料在 Sheet『Discoveries』分頁")
    return "\n".join(lines)


def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    # 1. 讀現有 watchlist 用來排除
    print(f"[{datetime.now()}] 讀取你的 Watchlist...")
    watchlist = read_watchlist()
    watchlist_ids = {str(r["stock_id"]) for r in watchlist}
    print(f"  → {len(watchlist_ids)} 檔已在 Watchlist，將排除")

    # 2. 去重後過濾掉已存在的
    universe = _dedupe_universe(SCAN_UNIVERSE)
    candidates = [(sid, name, cat) for sid, name, cat in universe if sid not in watchlist_ids]
    print(f"  → 掃描宇宙 {len(universe)} 檔，需分析 {len(candidates)} 檔")

    # 3. 逐檔評估
    print("開始評估...")
    results: list[dict] = []
    fail = 0
    for i, (sid, name, cat) in enumerate(candidates, 1):
        if i % 20 == 0 or i == len(candidates):
            print(f"  進度 [{i}/{len(candidates)}]")
        try:
            r = evaluate(sid, name)
        except Exception as e:
            fail += 1
            print(f"  ⚠️ {sid} {name} 失敗: {str(e)[:60]}")
            r = None
        if r:
            r["_category"] = cat
            results.append(r)
        time.sleep(0.6)  # 尊重 FinMind 速率限制

    print(f"  → 完成 {len(results)} 檔，失敗 {fail} 檔")

    # 4. 排序：BUY > WATCH > SKIP，再依分數
    order = {"BUY": 0, "WATCH": 1, "SKIP": 2, "ERROR": 3}
    actionable = [r for r in results if str(r.get("action", "")).upper() in ("BUY", "WATCH")]
    actionable.sort(key=lambda x: (order.get(x.get("action"), 4), -x.get("signal_score", 0)))
    top = actionable[:10]
    print(f"  → 找到 {len(actionable)} 檔達標，輸出 Top {len(top)}")

    # 5. 寫進 Sheet 的 Discoveries 分頁
    if top:
        print("寫回 Google Sheet (Discoveries)...")
        try:
            _write_discoveries(top)
        except Exception as e:
            print(f"⚠️ 寫 Sheet 失敗: {e}", file=sys.stderr)

    # 6. 推 Telegram
    print("發送 Telegram...")
    send_telegram(_format_message(top, total_scanned=len(candidates), watchlist_size=len(watchlist_ids)))
    print("✅ 完成")


if __name__ == "__main__":
    main()
