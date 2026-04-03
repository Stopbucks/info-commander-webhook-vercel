# ---------------------------------------------------------
# 程式碼：api/index.py (Vercel Serverless Webhook 路由)
# 職責：接收 TG 回覆 -> 萃取標題 -> 寫入 Supabase -> 喚醒 GHA
# 原則：極簡防崩潰。無論 TG 訊息，皆回傳 200 OK 給 TG。(帶有reply的訊息，則執行職責)
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

# 🎯 GHA 喚醒目標 (依據您的截圖設定)
GITHUB_USER = "Stopbucks"
GITHUB_REPO = "info-commander-deepthink"

# 初始化資料庫連線
sb = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# =========================================================
# 🚀 核心 Webhook 接收端點
# =========================================================
@app.route('/api/webhook', methods=['POST'])
def webhook():
    try:
        # 1. 接收並解析 TG 傳來的 JSON 數據包
        data = request.json
        if not data or 'message' not in data:
            return jsonify({"status": "ignored", "reason": "非一般訊息"}), 200

        msg = data['message']
        chat_id = str(msg.get('chat', {}).get('id', ''))

        # 2. 身份驗證：只處理來自「逆向工程指揮所 (-5121329081)」的訊息
        if chat_id != TELEGRAM_CHAT_ID:
            return jsonify({"status": "ignored", "reason": "非指定指揮所"}), 200

        # 3. 動作驗證：只處理「回覆 (Reply)」的訊息
        if 'reply_to_message' not in msg:
            return jsonify({"status": "ignored", "reason": "非回覆訊息"}), 200

        original_text = msg['reply_to_message'].get('text', '')
        user_command = msg.get('text', '').strip()

        # 4. 萃取標題：利用正規表達式尋找「📌 標題：」後面的文字
        title_match = re.search(r"📌\s*標題：\s*(.*)", original_text)
        if not title_match:
            return jsonify({"status": "ignored", "reason": "找不到標題特徵"}), 200

        target_title = title_match.group(1).strip()
        print(f"🎯 攔截指令: {user_command} | 目標: {target_title[:15]}...")

        # 5. 模糊搜尋任務 ID (使用 ilike)
        task_id = None
        status = "not_found"
        if sb:
            q_res = sb.table("mission_queue").select("id").ilike("episode_title", f"%{target_title}%").limit(1).execute()
            if q_res.data and len(q_res.data) > 0:
                task_id = q_res.data[0]['id']
                status = "pending"

        # 6. 寫入逆向工程任務表 (mission_reverse)
        if sb:
            payload = {
                "task_id": task_id,
                "target_prompt": user_command,
                "status": status
            }
            sb.table("mission_reverse").insert(payload).execute()
            print("✅ 任務已登錄 Supabase")

        # 7. 遠端喚醒 GitHub Actions (僅在成功找到任務時發射)
        if task_id and GITHUB_PAT:
            trigger_github_action()

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"💥 Webhook 崩潰: {str(e)}")
        # 🛡️ 絕對防禦：即使出錯，也要回傳 200，防止 TG 伺服器無限重試轟炸
        return jsonify({"status": "error"}), 200

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
