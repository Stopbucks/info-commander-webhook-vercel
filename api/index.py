# ---------------------------------------------------------
# 程式碼：api/index.py (Vercel Serverless Webhook 路由)
# 版本：V3.0 (絕對信標追蹤版)
# 職責：接收 TG 回覆 -> 提取任務 ID -> 寫入沙盒 -> 喚醒 GHA -> TG 回報
# [V3.0 升級] 徹底廢棄標題模糊比對。直接鎖定 TG 訊息中的 [任務ID前8碼] 進行精準資料庫對位。大幅提升效能與 100% 準確率。
# ---------------------------------------------------------
from flask import Flask, request, jsonify
import os, re, requests
from supabase import create_client

app = Flask(__name__)

# =========================================================
# ⚙️ 讀取 Vercel 環境變數
# =========================================================
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") 
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GITHUB_PAT = os.environ.get("GITHUB_PAT")
GITHUB_USER = os.environ.get("GITHUB_USER")
GITHUB_REPO = os.environ.get("GITHUB_REPO")

sb = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# =========================================================
# 🧠 絕對信標尋標器 
# =========================================================
def extract_task_id_beacon(text):
    """
    從 TG 戰報中精準提取任務 ID 前 8 碼。
    支援格式: [1234abcd], 〔1234abcd〕, 或是 標題：1234abcd
    """
    if not text: return None
    
    # 策略：尋找 8 碼的十六進位字串，通常被括號包圍或緊跟在標題後
    match = re.search(r"(?:\[|〔|標題：\s*)([a-f0-9]{8})(?:\]|〕|\s)", text.lower())
    if match:
        return match.group(1)
        
    # 保底策略：如果格式跑掉，直接在整段文字中找第一個連續的 8 碼十六進位
    match_fallback = re.search(r"\b([a-f0-9]{8})\b", text.lower())
    if match_fallback:
        return match_fallback.group(1)
        
    return None

# =========================================================
# 📡 TG 即時回報器
# =========================================================
def send_tg_reply(text, reply_to_message_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    try: requests.post(url, json=payload, timeout=5)
    except Exception as e: print(f"⚠️ TG回報失敗: {e}") 

# =========================================================
# 📡 遠端信號發射器 (喚醒 GHA)
# =========================================================
def trigger_github_action():
    if not all([GITHUB_PAT, GITHUB_USER, GITHUB_REPO]): return False
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/dispatches"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(url, headers=headers, json={"event_type": "trigger-deep-rethink"}, timeout=5)
        return resp.status_code == 204
    except: return False

# =========================================================
# 🚀 核心 Webhook 接收端點 
# =========================================================
@app.route('/api/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if not data or 'message' not in data: return jsonify({"status": "ignored"}), 200

        msg = data['message']
        chat_id = str(msg.get('chat', {}).get('id', ''))
        msg_id = msg.get('message_id')

        # 驗證身分與 Reply 模式
        if chat_id != TELEGRAM_CHAT_ID or 'reply_to_message' not in msg:
            return jsonify({"status": "ignored"}), 200

        original_text = msg['reply_to_message'].get('text', '')
        user_command = msg.get('text', '').strip()
        
        # 🚀 V3.0 升級：直接抽取信標 ID
        short_id = extract_task_id_beacon(original_text)

        if not short_id:
            send_tg_reply("❌ **【尋標失敗】**\n長官，無法在您回覆的訊息中找到 **[8碼任務ID]**。\n請確認該訊息是我們新版帶有 ID 的情報戰報。", msg_id)
            return jsonify({"status": "no_id_found"}), 200

        task_id = None
        status = "not_found"

        try:
            if sb:
                # 🚀 V3.0 升級：使用 LIKE 'id%' 進行主鍵前綴精準打擊
                q_res = sb.table("mission_queue").select("id, episode_title").like("id", f"{short_id}%").limit(1).execute()
                
                if q_res.data:
                    task_id = q_res.data[0]['id']
                    ep_title = q_res.data[0]['episode_title'][:20] # 抓取標題供回報顯示用
                    status = "awaiting_stt" 
                else:
                    ep_title = "未知節目"

                insert_payload = {
                    "task_id": task_id,
                    "target_prompt": user_command,
                    "status": status
                }
                
                if status == "not_found": insert_payload["error_log"] = f"ID Beacon missing in DB: {short_id}"
                sb.table("mission_reverse").insert(insert_payload).execute()
                
        except Exception as db_err:
            print(f"❌ 資料庫寫入異常: {db_err}")

        # 狀態分流：喚醒 GHA & 回報 TG
        if status == "awaiting_stt":
            gha_triggered = trigger_github_action()
            reply_text = f"✅ **【逆向工程：已收單】**\n🎯 鎖定座標：`[{short_id}] {ep_title}...`\n\n情報已寫入沙盒等待區。已呼叫 GHA 刺客部隊進行後續作業。\n⏳ 請長官靜候，戰報將自動空投！"
            if not gha_triggered:
                reply_text += "\n\n*(⚠️ 註：GHA 喚醒信號延遲，部隊將於 6 小時內例行巡邏時自動接管)*"
            send_tg_reply(reply_text, msg_id)
            
        elif status == "not_found":
            send_tg_reply(f"❌ **【信標失效】**\n長官，雖然成功提取了信標 `[{short_id}]`，但該任務在資料庫中已被抹除或無法對位。", msg_id)

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"🔥 核心防禦系統啟動: {str(e)}")
        return jsonify({"status": "fail_but_handled"}), 200

if __name__ == '__main__':
    app.run(port=5000)
