import datetime
import time
import requests
import json
import gspread
from google.oauth2.service_account import Credentials
import os

creds_json = os.environ.get("GOOGLE_CREDENTIALS")
if creds_json:
    print("✅ 成功讀到 GOOGLE_CREDENTIALS，長度:", len(creds_json))
else:
    print("❌ 沒有讀到 GOOGLE_CREDENTIALS")

def update_history(ws, hs_data, lev_price, date_time, import_data):
    hs_dict = {name[0]: idx for idx, name in enumerate(hs_data) if name}
    row_values = ws.row_values(1)
    next_free_cell_in_row = len(row_values) + 1
    total_cols = ws.col_count
    if next_free_cell_in_row > total_cols:
        ws.add_cols(next_free_cell_in_row - total_cols)

    result = "".join([char for char in (ws.cell(1, next_free_cell_in_row).address) if not char.isdigit()])
    ws.update([[date_time]], range_name=f'{result}1', value_input_option="RAW")

    final_column = [0] * len(hs_data)
    for i, item in enumerate(import_data):
        item_name = item[0] if item else None
        if item_name and item_name in hs_dict:
            index = hs_dict[item_name]
            if lev_price[i] and lev_price[i][0] != "":
                final_column[index] = lev_price[i][0]
            else:
                final_column[index] = 0

    values = [[val] for val in final_column]
    range_str = f'{result}2:{result}{len(hs_data)+1}'
    ws.update(values, range_name=range_str, value_input_option="RAW")

def main():
    creds_json = os.environ["GOOGLE_CREDENTIALS"]
    creds_dict = json.loads(creds_json)

    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]

    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)

    worksheet = gc.open('WFM').worksheet('匯入')

    # 取得所有 item slug
    url_slug = "https://api.warframe.market/v2/items"
    headers_slug = {"Language": "zh-hant"}
    resp_slug = requests.get(url_slug, headers=headers_slug)
    data_slug = resp_slug.json()

    tz = datetime.timezone(datetime.timedelta(hours=8))  # UTC+8
    time_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    data_list, slug_list, tags_list = [], [], []

    worksheet.clear()
    worksheet.append_row(["名稱","賣單數量", "0等最低賣價", "滿等最低賣價",
                          "買單數量", "0等最高買價", "滿等最高買價", "標籤", time_str])

    for item in data_slug["data"]:
        data_list.append(item["i18n"]["zh-hant"]["name"])
        slug_list.append(item["slug"])
        tags_list.append(item["tags"])

    print("開始匯入商品及價格")
    batch_size = 100
    for start in range(0, len(slug_list), batch_size):
        end = start + batch_size
        batch_slugs = slug_list[start:end]
        batch_rows = []

        for idx, slug in enumerate(batch_slugs, start=start):
            url_item = f"https://api.warframe.market/v2/orders/item/{slug}"
            resp_item = requests.get(url_item)

            if resp_item.status_code != 200:
                print(f"跳過: {slug}, 狀態碼={resp_item.status_code}")
                continue

            try:
                data_item = resp_item.json()
            except Exception as e:
                print(f"解析失敗: {slug}, 內容={resp_item.text[:100]}")
                continue

            all_orders = data_item["data"]

            buy_orders = [o for o in all_orders if o["type"] == "buy" and o["user"].get("status") == "ingame"]
            sell_orders = [o for o in all_orders if o["type"] == "sell" and o["user"].get("status") == "ingame"]

            item_info = data_slug["data"][idx]
            if "maxRank" in item_info:
                max_rank = item_info["maxRank"]

                rank0_buy = [o for o in buy_orders if o.get("rank", None) == 0]
                rankmax_buy = [o for o in buy_orders if o.get("rank", None) == max_rank]
                max0_buy = max(o["platinum"] / o["perTrade"] for o in rank0_buy) if rank0_buy else "0"
                maxmax_buy = max(o["platinum"] / o["perTrade"] for o in rankmax_buy) if rankmax_buy else "0"

                rank0_sell = [o for o in sell_orders if o.get("rank", None) == 0]
                rankmax_sell = [o for o in sell_orders if o.get("rank", None) == max_rank]
                min0_sell = min(o["platinum"] / o["perTrade"] for o in rank0_sell) if rank0_sell else "0"
                minmax_sell = min(o["platinum"] / o["perTrade"] for o in rankmax_sell) if rankmax_sell else "0"

            else:
                max0_buy = max(o["platinum"] / o["perTrade"] for o in buy_orders) if buy_orders else "0"
                maxmax_buy = max0_buy
                min0_sell = min(o["platinum"] / o["perTrade"] for o in sell_orders) if sell_orders else "0"
                minmax_sell = min0_sell

            if max0_buy == "0" and maxmax_buy == "0" and min0_sell == "0" and minmax_sell == "0":
                continue

            flat_row = [data_list[idx], len(sell_orders), min0_sell, minmax_sell,
                        len(buy_orders), max0_buy, maxmax_buy] + tags_list[idx]
            batch_rows.append(flat_row)

            time.sleep(0.1)

        if batch_rows:
            try:
                worksheet.append_rows(batch_rows, value_input_option="RAW")
                print(f"✅ 已寫入 {len(batch_rows)} 筆 (範圍 {start} ~ {end})")
            except Exception as e:
                print("❌ 寫入失敗:", e)

    print("匯入商品及價格結束")

    # ===== 匯入歷史價格 =====
    print("開始匯入歷史資料庫")
    ws1 = gc.open('WFM').worksheet('匯入')
    ws_s0 = gc.open('WFM').worksheet('歷史賣單價格(0等)')
    ws_sf = gc.open('WFM').worksheet('歷史賣單價格(滿等)')
    ws_b0 = gc.open('WFM').worksheet('歷史買單價格(0等)')
    ws_bf = gc.open('WFM').worksheet('歷史買單價格(滿等)')
    ws_sc = gc.open('WFM').worksheet('歷史賣單數量')
    ws_bc = gc.open('WFM').worksheet('歷史買單數量')
    ws_sp0 = gc.open('WFM').worksheet('歷史價差(0等)')
    ws_spf = gc.open('WFM').worksheet('歷史價差(滿等)')
    
    date_time = ws1.acell('I1').value  # 時間戳在第 I 欄
    import_data = ws1.get('A2:A')      # 商品名
    sell_count = ws1.get('B2:B')       # 賣單數量
    lev0_sell = ws1.get('C2:C')        # 0階最低賣價
    levf_sell = ws1.get('D2:D')        # 滿階最低賣價
    buy_count = ws1.get('E2:E')        # 買單數量
    lev0_buy = ws1.get('F2:F')         # 0階最高買價
    levf_buy = ws1.get('G2:G')         # 滿階最高買價
    
    hs0_data = ws_s0.get('A2:A')
    hsf_data = ws_sf.get('A2:A')
    hb0_data = ws_b0.get('A2:A')
    hbf_data = ws_bf.get('A2:A')
    sc_data = ws_sc.get('A2:A')
    bc_data = ws_bc.get('A2:A')
    sp0_data = ws_sp0.get('A2:A')
    spf_data = ws_spf.get('A2:A')
    
    def update_history(ws, hs_data, lev_price, date_time):
        hs_dict = {name[0]: idx for idx, name in enumerate(hs_data) if name}
        row_values = ws.row_values(1)
        next_free_cell_in_row = len(row_values) + 1
        total_cols = ws.col_count
        if next_free_cell_in_row > total_cols:
            ws.add_cols(next_free_cell_in_row - total_cols)
        result = "".join([char for char in (ws.cell(1, next_free_cell_in_row).address) if not char.isdigit()])
        ws.update([[date_time]], range_name=f'{result}1', value_input_option="RAW")
    
        final_column = [0] * len(hs_data)
        for i, item in enumerate(import_data):
            item_name = item[0] if item else None
            if item_name and item_name in hs_dict:
                index = hs_dict[item_name]
                if lev_price[i] and lev_price[i][0] != "":
                    final_column[index] = lev_price[i][0]
                else:
                    final_column[index] = 0
    
        values = [[val] for val in final_column]
        range_str = f'{result}2:{result}{len(hs_data)+1}'
        ws.update(values, range_name=range_str, value_input_option="RAW")
    
    # 更新八個歷史表
    update_history(ws_s0, hs0_data, lev0_sell, date_time)
    print(f"已寫入 {date_time} 的0等賣單價格")
    update_history(ws_sf, hsf_data, levf_sell, date_time)
    print(f"已寫入 {date_time} 的滿等賣單價格")
    update_history(ws_b0, hb0_data, lev0_buy, date_time)
    print(f"已寫入 {date_time} 的0等買單價格")
    update_history(ws_bf, hbf_data, levf_buy, date_time)
    print(f"已寫入 {date_time} 的滿等買單價格")
    update_history(ws_sc, sc_data, sell_count, date_time)
    print(f"已寫入 {date_time} 的賣單數量")
    update_history(ws_bc, bc_data, buy_count, date_time)
    print(f"已寫入 {date_time} 的買單數量")
    
    # 價差計算
    spread0, spreadf = [], []
    for i in range(len(import_data)):
        if not import_data[i] or import_data[i][0].strip() == "":
            spread0.append([""]); spreadf.append([""])
            continue
        try:
            sell0 = int(lev0_sell[i][0]) if lev0_sell[i] and lev0_sell[i][0].isdigit() else None
            buy0  = int(lev0_buy[i][0])  if lev0_buy[i] and lev0_buy[i][0].isdigit()  else None
            spread0.append([str(sell0 - buy0)] if sell0 is not None and buy0 is not None else [""])
        except Exception: spread0.append([""])
        try:
            sellf = int(levf_sell[i][0]) if levf_sell[i] and levf_sell[i][0].isdigit() else None
            buyf  = int(levf_buy[i][0])  if levf_buy[i] and levf_buy[i][0].isdigit()  else None
            spreadf.append([str(sellf - buyf)] if sellf is not None and buyf is not None else [""])
        except Exception: spreadf.append([""])
    
    update_history(ws_sp0, sp0_data, spread0, date_time)
    print(f"已寫入 {date_time} 的0等價差")
    update_history(ws_spf, spf_data, spreadf, date_time)
    print(f"已寫入 {date_time} 的滿等價差")
    print("結束匯入今日歷史資料")


if __name__ == "__main__":
    main()
