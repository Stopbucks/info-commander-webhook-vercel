# ---------------------------------------------------------
# 程式碼：api/index.py (Vercel Serverless Webhook 路由 V2.1 智慧尋標版)
# 職責：接收 TG 回覆 -> 智慧萃取標題 -> 寫入 Supabase -> 喚醒 GHA
# 原則：極簡防崩潰。無論 TG 訊息，皆回傳 200 OK 給 TG。(帶有reply的訊息，則執行職責)
# 修改：多數情況都是去叫 GHA 執行後續複雜任務
# ---------------------------------------------------------
from flask import Flask, request, jsonify
import os, re, requests
from supabase import create_client

app = Flask(__name__)

# =========================================================
# ⚙️ 讀取 Vercel 環境變數
# =========================================================
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") 
GITHUB_PAT = os.environ.get("GITHUB_PAT")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# 💡 防呆註解：這裡是填寫「新兵 DeepRethink_bot」的金鑰！
# 絕對不要填成舊廣播兵 (Info Commander) 的金鑰，本質完全不同！
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# 🎯 GHA 喚醒目標
GITHUB_USER = "Stopbucks"
GITHUB_REPO = "info-commander-deepthink"

# 初始化資料庫連線
sb = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# =========================================================
# 🧠 智慧尋標器 (多重正規表達式)
# =========================================================
def extract_title(text):
    """從各種非標準格式中，智能萃取出最可能的標題文字"""
    if not text: return None
    
    # 策略 1：尋找明確的「📌 標題：」或「標題：」
    match = re.search(r"(?:📌\s*標題：|標題：)\s*(.*)", text)
    if match: return match.group(1).strip()
    
    # 策略 2：如果沒有標籤，直接抓取前 3 行內最長的那一行當作特徵字
    lines = [line.strip() for line in text.split('\n') if line.strip()][:3]
    if lines:
        return max(lines, key=len) # 抓取最長的一行，通常這就是英文標題
        
    return None



# =========================================================
# 🚀 核心 Webhook 接收端點 (防彈裝甲強化版)
# =========================================================
@app.route('/api/webhook', methods=['POST'])
def webhook():
    """接收 TG 回覆，確保不論發生什麼，都回傳 200 OK 給 Telegram"""
    try:
        # 1. 解析數據包
        data = request.json
        if not data or 'message' not in data:
            return jsonify({"status": "ignored"}), 200

        msg = data['message']
        chat_id = str(msg.get('chat', {}).get('id', ''))

        # 2. 身份與動作驗證 (僅限指揮所與回覆訊息)
        if chat_id != TELEGRAM_CHAT_ID or 'reply_to_message' not in msg:
            return jsonify({"status": "ignored"}), 200

        # 3. 智慧萃取標題與指令
        original_text = msg['reply_to_message'].get('text', '')
        user_command = msg.get('text', '').strip()
        target_title = extract_title(original_text)

        if not target_title:
            return jsonify({"status": "no_title_found"}), 200

        # 4. 準備寫入資料庫 (隔離執行，確保資料優先入庫)
        task_id = None
        status = "not_found"
        
        # 💡 淨化標題：移除 Emoji 與特殊符號，大幅提升比對成功率
        clean_title = re.sub(r'[^\w\s,.?\'"-]', '', target_title).strip()
        search_keyword = clean_title[:30] 

        try:
            if sb:
                # 模糊搜尋母表任務
                q_res = sb.table("mission_queue").select("id").ilike("episode_title", f"%{search_keyword}%").limit(1).execute()
                if q_res.data:
                    task_id = q_res.data[0]['id']
                    status = "pending"
                
                # 寫入逆向任務表 (這是最關鍵的一步)
                insert_payload = {
                    "task_id": task_id,
                    "target_prompt": user_command,
                    "status": status
                }
                # 💡 留下證據：若仍找不到，將原始標題存入 error_log 供 GHA 參考與發報
                if status == "not_found":
                    insert_payload["error_log"] = f"Missing Title: {target_title}"

                sb.table("mission_reverse").insert(insert_payload).execute()
                print(f"✅ 任務存檔成功: {status}")
        except Exception as db_err:
            print(f"❌ 資料庫寫入異常: {db_err}") # 僅記錄，不崩潰

        # 5. 嘗試喚醒 GitHub Actions (💡 放寬限制：pending 與 not_found 皆喚醒)
        if status in ["pending", "not_found"] and GITHUB_PAT:
            try:
                trigger_github_action()
            except Exception as gha_err:
                # 🛡️ 即使 GHA 喚醒失敗，由於資料已在 Supabase 設為 pending，保底巡邏會接手
                print(f"⚠️ GHA 喚醒暫時失靈: {gha_err}")

        # 🎖️ 優雅退場：給 Telegram 一個交代
        return jsonify({"status": "success"}), 200

    except Exception as e:
        # 🛡️ 最終防線：捕捉所有非預期錯誤，嚴禁拋出 500 錯誤
        print(f"🔥 核心防禦系統啟動: {str(e)}")
        return jsonify({"status": "fail_but_handled"}), 200


# =========================================================
# 📡 遠端信號發射器
# =========================================================
def trigger_github_action():
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/dispatches"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Content-Type": "application/json"
    }
    data = {"event_type": "trigger-deep-rethink"} # 必須與 YAML 檔內的 types 一致
    
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=5)
        if resp.status_code == 204:
            print("🚀 GitHub 時光刺客已成功喚醒！")
        else:
            print(f"⚠️ 喚醒失敗: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"💥 發射器故障: {e}")

if __name__ == '__main__':
    app.run(port=5000)
