import os
import gspread
from google.oauth2.service_account import Credentials

def test_service_account_access():
    # 從 GitHub Secrets 或本地環境讀取憑證
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        print("❌ 沒有讀到 GOOGLE_CREDENTIALS")
        return

    # 建立憑證物件
    creds = Credentials.from_service_account_info(
        eval(creds_json),  # 注意：在 GitHub Actions 裡 GOOGLE_CREDENTIALS 是字串，要轉成 dict
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )

    # 嘗試連線 Google Sheets
    try:
        gc = gspread.authorize(creds)
        # 打開你的試算表（替換成實際名稱）
        sh = gc.open("WFM")
        worksheet = sh.worksheet("匯入")
        print("✅ 成功存取試算表:", sh.title)
        print("✅ 成功存取工作表:", worksheet.title)
    except Exception as e:
        print("❌ 存取失敗:", e)

if __name__ == "__main__":
    test_service_account_access()
