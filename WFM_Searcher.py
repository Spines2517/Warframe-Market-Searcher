import os
import json
import gspread
from google.oauth2.service_account import Credentials
import datetime

def main():
    # 讀取 Secrets
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        print("❌ 沒有讀到 GOOGLE_CREDENTIALS")
        return

    creds_dict = json.loads(creds_json)

    # 指定正確的 scope
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)

    # 打開試算表
    worksheet = gc.open('WFM').worksheet('匯入')

    # 取得時間戳
    tz = datetime.timezone(datetime.timedelta(hours=8))  # UTC+8
    time_str = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    # 測試寫入一行資料
    try:
        worksheet.append_row(["測試寫入", "123", "456", "789", "測試標籤", time_str], value_input_option="RAW")
        print("✅ 已成功寫入一行測試資料")
    except Exception as e:
        print("❌ 寫入失敗:", e)

if __name__ == "__main__":
    main()
