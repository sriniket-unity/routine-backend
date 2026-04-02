from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime, timedelta
import os
import google.generativeai as genai
import json
import pytz
import re 
import traceback

app = Flask(__name__)
CORS(app)

# --- 🌍 CONFIGURATION ---
IST = pytz.timezone('Asia/Kolkata')
# Initialize with the 2026 SDK standard
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# --- 📊 SHEETS CONNECTION ---
timetable_ws = None
logs_ws = None
chat_logs_ws = None 

def init_sheets():
    global timetable_ws, logs_ws, chat_logs_ws
    try:
        creds_json = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        if creds_json:
            client = gspread.service_account_from_dict(json.loads(creds_json))
            sheet = client.open("overall_db")
            timetable_ws = sheet.worksheet("Timetable")
            logs_ws = sheet.worksheet("Logs")
            chat_logs_ws = sheet.worksheet("ChatLogs")
            print("✅ Sheets Status: Gemini 3 Systems Synchronized.")
    except Exception as e:
        print(f"❌ Sheets Error: {e}")

init_sheets()

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def health():
    return jsonify({"service": "Routine Flow Architect", "version": "5.5.2", "status": "Online"}), 200

@app.route('/chat', methods=['POST'])
def chat():
    try:
        if not chat_logs_ws: init_sheets()
        user_msg = request.json.get('message')

        # 1. Fetch Schedule (Manual zip to ignore merged headers)
        all_tt = timetable_ws.get_all_values()
        tt_headers = [h.strip() for h in all_tt[1] if h.strip()]
        lean_tt = [dict(zip(tt_headers, r)) for r in all_tt[2:] if any(r)][-10:]

        # 2. Fetch History
        all_chat = chat_logs_ws.get_all_values()
        memory = []
        if len(all_chat) > 1:
            chat_headers = [h.strip() for h in all_chat[0] if h.strip()]
            memory = [dict(zip(chat_headers, r)) for r in all_chat[1:] if any(r)][-6:]

        # 3. Prompt Construction
        prompt = f"""
        System: You are 'Routine Flow Architect' for Sriniket.
        Context: Sriniket is recovering from an accident.
        Timetable Data: {json.dumps(lean_tt)}
        Memory: {json.dumps(memory)}
        
        User Input: {user_msg}
        
        Mandatory Format for Schedule Changes:
        ACTION_RECS: {{"action_target": "Activity Name", "new_val": "1.0h", "reason": "Reason for change"}}
        """

        # 🚀 RESTORED: Gemini 3 Flash
        model = genai.GenerativeModel('gemini-3-flash')
        response = model.generate_content(prompt)
        ai_text = response.text

        # 4. Persistence
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        chat_logs_ws.append_rows([[ts, "User", user_msg], [ts, "AI", ai_text]])

        return jsonify({"status": "success", "text": ai_text}), 200

    except Exception as e:
        # Full Traceback for Render Logs
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": f"QA_DEBUG: {str(e)}"}), 500

# Other endpoints (get_schedule, log_session, clear_chat, etc.) remain identical
@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        if not timetable_ws: init_sheets()
        all_val = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_val[1] if h.strip()] 
        data = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        return jsonify({"status": "success", "data": data})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        d = request.json
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        logs_ws.append_row([ts, d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)])
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/clear_chat', methods=['DELETE'])
def clear_chat():
    try:
        records = chat_logs_ws.get_all_values()
        if len(records) > 1: chat_logs_ws.delete_rows(2, len(records))
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    try:
        data = request.json
        pattern = re.compile(rf'^{re.escape(data.get("activity"))}$', re.IGNORECASE)
        cell = timetable_ws.find(pattern)
        if cell:
            timetable_ws.update_cell(cell.row, cell.col + 1, data.get('new_val'))
            return jsonify({"status": "success"}), 200
        return jsonify({"status": "error"}), 404
    except Exception as e: return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)