import datetime
import json
import os
import sys
import time

import gspread
import requests
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

# ===================== 設定 =====================
API_BASE = "https://api.warframe.market/v2"
HEADERS = {
    "User-Agent": "WFM-Searcher/1.0",
    "Accept": "application/json",
    "Language": "zh-hant",
    "Platform": "pc",
}
MIN_INTERVAL = 0.35          # 每次請求最小間隔（秒），約 3 req/s 以下
RETRIES = 3                  # 單一請求最多嘗試次數
BACKOFF_BASE = 5             # 失敗後等待 5、10、20 秒
MAX_CONSECUTIVE_FAILS = 8    # 連續失敗幾個物品就判定被擋，直接中止
MIN_SUCCESS_RATIO = 0.8      # 成功抓到的物品比例低於此值，就不寫入任何資料
# =================================================

session = requests.Session()
session.headers.update(HEADERS)
_last_request = 0.0


def get_json(url):
    """帶節流、重試與退避的 GET。成功回傳 dict，失敗回傳 None。"""
    global _last_request
    for attempt in range(RETRIES):
        wait = MIN_INTERVAL - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()

        try:
            resp = session.get(url, timeout=30)
        except requests.RequestException as e:
            print(f"[請求例外] {url} {e}", flush=True)
            time.sleep(BACKOFF_BASE * 2 ** attempt)
            continue

        ctype = resp.headers.get("Content-Type", "")
        if resp.status_code == 200 and "json" in ctype:
            try:
                return resp.json()
            except ValueError:
                print(f"[JSON 解析失敗] {url} body={resp.text[:150]!r}", flush=True)
                return None

        print(f"[{resp.status_code}] {ctype} {url} body={resp.text[:150]!r}", flush=True)
        if resp.status_code in (403, 429, 500, 502, 503, 504):
            time.sleep(BACKOFF_BASE * 2 ** attempt)
            continue
        return None  # 404 等不需要重試
    return None


def clean(x):
    """四捨五入到小數 2 位；整數值轉成 int。"""
    x = round(x, 2)
    return int(x) if x == int(x) else x


def unit_price(order):
    return order["platinum"] / (order.get("perTrade") or 1)


def best(orders, pick, rank=None):
    """取得最高買價/最低賣價（每單位）。沒有符合的訂單回傳 0。"""
    if rank is not None:
        orders = [o for o in orders if o.get("rank") == rank]
    return clean(pick(unit_price(o) for o in orders)) if orders else 0


def update_history(ws, values_by_name, time_str, fill=0):
    """在歷史表最右邊新增一欄：第 1 列寫時間，A 欄名稱對應的列寫數值。"""
    hs_names = ws.col_values(1)[1:]  # A2:A
    if not hs_names:
        print(f"⚠️ 工作表 {ws.title} 的 A 欄沒有商品名，略過")
        return
    hs_dict = {name: idx for idx, name in enumerate(hs_names) if name}

    col = len(ws.row_values(1)) + 1
    if col > ws.col_count:
        ws.add_cols(col - ws.col_count)

    ws.update([[time_str]], range_name=rowcol_to_a1(1, col), value_input_option="RAW")

    column = [fill] * len(hs_names)
    for name, val in values_by_name.items():
        idx = hs_dict.get(name)
        if idx is not None:
            column[idx] = val

    range_str = f"{rowcol_to_a1(2, col)}:{rowcol_to_a1(len(hs_names) + 1, col)}"
    ws.update([[v] for v in column], range_name=range_str, value_input_option="RAW")


def main():
    started = time.time()

    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        sys.exit("❌ 沒有讀到 GOOGLE_CREDENTIALS")
    print("✅ 成功讀到 GOOGLE_CREDENTIALS，長度:", len(creds_json), flush=True)

    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("WFM")

    tz = datetime.timezone(datetime.timedelta(hours=8))  # UTC+8
    time_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    # ---------- 1. 取得所有物品清單 ----------
    data_slug = get_json(f"{API_BASE}/items")
    if not data_slug or "data" not in data_slug:
        sys.exit("❌ 取得物品清單失敗，中止（未修改任何表格）")
    items = data_slug["data"]
    print(f"物品總數: {len(items)}", flush=True)

    # ---------- 2. 逐項抓訂單（全部先存在記憶體） ----------
    records = []
    ok_count = 0
    fail_count = 0
    consecutive_fails = 0

    print("開始抓取商品及價格", flush=True)
    for n, item in enumerate(items, start=1):
        slug = item["slug"]
        i18n = item.get("i18n", {})
        name = (i18n.get("zh-hant") or i18n.get("en") or {}).get("name", slug)
        tags = item.get("tags", [])

        data_item = get_json(f"{API_BASE}/orders/item/{slug}")
        if data_item is None or "data" not in data_item:
            fail_count += 1
            consecutive_fails += 1
            if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                sys.exit(f"❌ 連續 {consecutive_fails} 個物品失敗，疑似被擋（403/429），中止。"
                         f"尚未修改任何表格。")
            continue

        consecutive_fails = 0
        ok_count += 1

        orders = data_item["data"]
        buy_orders = [o for o in orders if o["type"] == "buy" and o["user"].get("status") == "ingame"]
        sell_orders = [o for o in orders if o["type"] == "sell" and o["user"].get("status") == "ingame"]

        max_rank = item.get("maxRank")
        if max_rank is not None:
            max0_buy = best(buy_orders, max, 0)
            maxmax_buy = best(buy_orders, max, max_rank)
            min0_sell = best(sell_orders, min, 0)
            minmax_sell = best(sell_orders, min, max_rank)
        else:
            max0_buy = best(buy_orders, max)
            maxmax_buy = max0_buy
            min0_sell = best(sell_orders, min)
            minmax_sell = min0_sell

        if max0_buy == 0 and maxmax_buy == 0 and min0_sell == 0 and minmax_sell == 0:
            continue

        records.append({
            "name": name,
            "sell_n": len(sell_orders), "min0_sell": min0_sell, "minmax_sell": minmax_sell,
            "buy_n": len(buy_orders), "max0_buy": max0_buy, "maxmax_buy": maxmax_buy,
            "tags": tags,
        })

        if n % 100 == 0:
            print(f"進度 {n}/{len(items)}｜成功 {ok_count}｜失敗 {fail_count}｜"
                  f"有掛單 {len(records)}｜{time.time() - started:.0f}s", flush=True)

    print(f"抓取結束：成功 {ok_count}，失敗 {fail_count}，有掛單 {len(records)}", flush=True)

    # ---------- 3. 資料健全性檢查：不合格就完全不寫入 ----------
    if ok_count / len(items) < MIN_SUCCESS_RATIO or not records:
        sys.exit(f"❌ 成功率過低（{ok_count}/{len(items)}），資料不可信，"
                 f"中止，不寫入「匯入」表與歷史表")

    # ---------- 4. 寫入「匯入」表 ----------
    worksheet = sh.worksheet("匯入")
    worksheet.clear()
    header = ["名稱", "賣單數量", "0等最低賣價", "滿等最低賣價",
              "買單數量", "0等最高買價", "滿等最高買價", "標籤", time_str]
    rows = [[r["name"], r["sell_n"], r["min0_sell"], r["minmax_sell"],
             r["buy_n"], r["max0_buy"], r["maxmax_buy"]] + r["tags"] for r in records]

    worksheet.append_rows([header], value_input_option="RAW")
    chunk = 500
    for s in range(0, len(rows), chunk):
        worksheet.append_rows(rows[s:s + chunk], value_input_option="RAW")
        print(f"✅ 已寫入 {min(s + chunk, len(rows))}/{len(rows)} 筆", flush=True)
    print("匯入商品及價格結束", flush=True)

    # ---------- 5. 歷史資料 ----------
    print("開始匯入歷史資料庫", flush=True)

    def by_name(key):
        return {r["name"]: r[key] for r in records}

    def spread(sell_key, buy_key):
        out = {}
        for r in records:
            s, b = r[sell_key], r[buy_key]
            out[r["name"]] = clean(s - b) if s > 0 and b > 0 else ""
        return out

    history_jobs = [
        ("歷史賣單價格(0等)", by_name("min0_sell"), 0, "0等賣單價格"),
        ("歷史賣單價格(滿等)", by_name("minmax_sell"), 0, "滿等賣單價格"),
        ("歷史買單價格(0等)", by_name("max0_buy"), 0, "0等買單價格"),
        ("歷史買單價格(滿等)", by_name("maxmax_buy"), 0, "滿等買單價格"),
        ("歷史賣單數量", by_name("sell_n"), 0, "賣單數量"),
        ("歷史買單數量", by_name("buy_n"), 0, "買單數量"),
        ("歷史價差(0等)", spread("min0_sell", "max0_buy"), "", "0等價差"),
        ("歷史價差(滿等)", spread("minmax_sell", "maxmax_buy"), "", "滿等價差"),
    ]

    for sheet_name, values, fill, label in history_jobs:
        update_history(sh.worksheet(sheet_name), values, time_str, fill=fill)
        print(f"已寫入 {time_str} 的{label}", flush=True)
        time.sleep(1.5)  # 避免 Google Sheets 寫入配額（每分鐘 60 次）

    print(f"結束匯入今日歷史資料，總耗時 {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
