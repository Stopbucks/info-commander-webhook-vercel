# ---------------------------------------------------------
# 程式碼：api/index.py (Vercel Serverless Webhook 路由)
# 版本：V2.2.1 (狀態機對接 )
# 職責：接收 TG 回覆 -> 智慧萃取標題 -> 寫入 Supabase沙盒 -> 喚醒 GHA -> TG 狀態回報
# 快速完成，重型任務交由GHA
# ---------------------------------------------------------
from flask import Flask, request, jsonify
import os, re, requests
from supabase import create_client

app = Flask(__name__)

# =========================================================
# ⚙️ 讀取 Vercel 環境變數 (全隱蔽架構)
# =========================================================
# TG 通訊與認證
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") 
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Supabase 資料庫連線
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# GitHub Actions 喚醒金鑰與目標 
GITHUB_PAT = os.environ.get("GITHUB_PAT")
GITHUB_USER = os.environ.get("GITHUB_USER")
GITHUB_REPO = os.environ.get("GITHUB_REPO")

# 初始化資料庫連線 (防呆機制：保持 None，避免啟動崩潰)
sb = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# =========================================================
# 🧠 智慧尋標器 
# =========================================================
def extract_title(text):
    """從各種非標準格式的 TG 訊息中，智能萃取出最可能的標題文字"""
    if not text: return None
    
    # 策略 1：尋找明確的「📌 標題：」或「標題：」標籤
    match = re.search(r"(?:📌\s*標題：|標題：)\s*(.*)", text)
    if match: return match.group(1).strip()
    
    # 策略 2：如果沒有標籤，抓取前 3 行內最長的那一行當作特徵字 (通常為標題)
    lines = [line.strip() for line in text.split('\n') if line.strip()][:3]
    if lines:
        return max(lines, key=len) 
        
    return None

# =========================================================
# 📡 TG 即時回報器
# =========================================================
def send_tg_reply(text, reply_to_message_id=None):
    """將 Vercel 的接單狀態，即時回報給指揮官的 TG 對話框"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    # 若有提供原始訊息 ID，則使用 Reply 模式回覆
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        # 僅印出錯誤，不阻礙主流程 (確保 Webhook 始終回傳 200)
        print(f"⚠️ TG回報失敗: {e}") 

# =========================================================
# 📡 遠端信號發射器 (喚醒 GHA)
# =========================================================
def trigger_github_action():
    """使用 PAT 敲擊 GitHub API，發送 trigger-deep-rethink 暗號喚醒 GHA"""
    if not all([GITHUB_PAT, GITHUB_USER, GITHUB_REPO]):
        print("⚠️ 缺少 GitHub 環境變數，無法喚醒 GHA。")
        return False

    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/dispatches"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Content-Type": "application/json"
    }
    data = {"event_type": "trigger-deep-rethink"} 
    
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=5)
        if resp.status_code == 204:
            print("🚀 GHA DeepThink 回溯已啟動！")
            return True
        else:
            print(f"⚠️ 喚醒失敗: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"💥 發射器故障: {e}")
        return False

# =========================================================
# 🚀 核心 Webhook 接收端點 
# =========================================================
@app.route('/api/webhook', methods=['POST'])
def webhook():
    """接收 TG 回覆，確保不論發生什麼，都優雅處理並回傳 200 OK"""
    try:
        # 1. 解析數據包
        data = request.json
        if not data or 'message' not in data:
            return jsonify({"status": "ignored"}), 200

        msg = data['message']
        chat_id = str(msg.get('chat', {}).get('id', ''))
        msg_id = msg.get('message_id') # 取得使用者發送的指令訊息 ID

        # 2. 身份與動作驗證 (僅限指揮官，且必須是 Reply 訊息)
        if chat_id != TELEGRAM_CHAT_ID or 'reply_to_message' not in msg:
            return jsonify({"status": "ignored"}), 200

        # 3. 智慧萃取標題與指令
        original_text = msg['reply_to_message'].get('text', '')
        user_command = msg.get('text', '').strip()
        target_title = extract_title(original_text)

        if not target_title:
            return jsonify({"status": "no_title_found"}), 200

        # 4. 準備寫入資料庫
        task_id = None
        status = "not_found"
        # 淨化標題：移除 Emoji 與特殊符號，取前 30 字元進行模糊比對
        clean_title = re.sub(r'[^\w\s,.?\'"-]', '', target_title).strip()
        search_keyword = clean_title[:30] 

        try:
            if sb:
                # 模糊搜尋母表任務
                q_res = sb.table("mission_queue").select("id").ilike("episode_title", f"%{search_keyword}%").limit(1).execute()
                if q_res.data:
                    task_id = q_res.data[0]['id']
                    # 🚀 進入四檔狀態機的起點
                    status = "awaiting_stt" 
                
                # 寫入逆向任務沙盒 (註：Supabase DB Trigger 會在此時自動補齊 r2_url 與 stt_text)
                insert_payload = {
                    "task_id": task_id,
                    "target_prompt": user_command,
                    "status": status
                }
                
                # 留下尋標失敗證據
                if status == "not_found":
                    insert_payload["error_log"] = f"Missing Title: {target_title}"

                sb.table("mission_reverse").insert(insert_payload).execute()
                print(f"✅ 任務存檔成功: {status}")
                
        except Exception as db_err:
            print(f"❌ 資料庫寫入異常: {db_err}")

        # 5. 狀態分流：喚醒 GHA & 回報 TG
        if status == "awaiting_stt":
            # 嘗試喚醒 GHA
            gha_triggered = trigger_github_action()
            
            # 組合 TG 即時回報訊息
            reply_text = f"✅ **【逆向工程：已收單】**\n🎯 鎖定目標：`{search_keyword}...`\n\n情報已寫入沙盒等待區。已呼叫 GHA 刺客部隊進行後續作業。\n⏳ 請長官靜候，戰報將自動空投！"
            
            # 若 GHA 喚醒失敗，加入保底提示
            if not gha_triggered:
                reply_text += "\n\n*(⚠️ 註：GHA 喚醒信號延遲，部隊將於 6 小時內例行巡邏時自動接管)*"
                
            send_tg_reply(reply_text, msg_id)
            
        elif status == "not_found":
            # 尋標失敗回報
            send_tg_reply(f"❌ **【尋標失敗】**\n長官，無法在資料庫找到標題匹配的音檔：\n`{search_keyword}`\n請確認回覆的訊息是否為標準情報戰報。", msg_id)

        # 🎖️ 優雅退場：給 Telegram API 一個 200 OK 交代，防止 Webhook 瘋狂重試
        return jsonify({"status": "success"}), 200

    except Exception as e:
        # 🛡️ 最終防線：捕捉所有非預期錯誤，嚴禁拋出 500 錯誤導致 Vercel 當機
        print(f"🔥 核心防禦系統啟動: {str(e)}")
        return jsonify({"status": "fail_but_handled"}), 200

if __name__ == '__main__':
    app.run(port=5000)
