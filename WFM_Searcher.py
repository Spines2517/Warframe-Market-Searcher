import os
import json
import gspread
from google.oauth2.service_account import Credentials

def main():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        print("❌ 沒有讀到 GOOGLE_CREDENTIALS")
        return

    creds_dict = json.loads(creds_json)  # Secrets 是字串，要轉成 dict
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)

    try:
        sh = gc.open("WFM")  # 替換成你的試算表名稱
        worksheet = sh.worksheet("匯入")
        print("✅ 成功存取試算表:", sh.title)
        print("✅ 成功存取工作表:", worksheet.title)
    except Exception as e:
        print("❌ 存取失敗:", e)

if __name__ == "__main__":
    main()
