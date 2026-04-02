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
import logging

app = Flask(__name__)
CORS(app)

# 📝 Enable Logging
logging.basicConfig(level=logging.INFO)

# --- 🌍 CONFIGURATION ---
IST = pytz.timezone('Asia/Kolkata')
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
            app.logger.info("✅ Sheets Status: All Systems Operational.")
            return True
    except Exception as e:
        app.logger.error(f"❌ Sheets Error: {e}")
        return False

# Initialize on boot
init_sheets()

# --- 🛠️ HELPERS ---
def sanitize_ts(ts_str):
    try:
        parts = ts_str.split(' ')
        h, m = parts[1].split(':')
        return f"{parts[0]} {h.zfill(2)}:{m.zfill(2)}"
    except: return ts_str

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def health():
    return jsonify({"service": "Routine Flow Architect", "version": "5.4.5", "status": "Ready"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        if not timetable_ws: init_sheets()
        all_val = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_val[1]] 
        data = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        return jsonify({"status": "success", "data": data})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    try:
        if not logs_ws: init_sheets()
        all_logs = logs_ws.get_all_records()
        if not all_logs: return jsonify({"status": "success", "overall": None, "week": None}), 200
        now = datetime.now(IST)
        start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)

        def process_subset(subset):
            if not subset: return {"study": 0, "adherence": 0, "debt": 0, "chart": [0.0]*7}
            total_study = sum(float(r.get('Actual (hrs)') or 0) for r in subset)
            total_debt = sum(float(r.get('Time Debt') or 0) for r in subset)
            completed = sum(1 for r in subset if float(r.get('Actual (hrs)') or 0) > 0)
            adherence = round((completed / len(subset)) * 100)
            chart = [0.0] * 7
            for r in subset:
                try:
                    dt = datetime.strptime(sanitize_ts(r.get('Timestamp', '')), '%Y-%m-%d %H:%M')
                    chart[dt.weekday()] += float(r.get('Actual (hrs)') or 0)
                except: continue
            return {"study": round(total_study, 1), "adherence": adherence, "debt": round(total_debt, 1), "chart": chart}

        overall = process_subset(all_logs)
        week_logs = [r for r in all_logs if IST.localize(datetime.strptime(sanitize_ts(r.get('Timestamp', '')), '%Y-%m-%d %H:%M')) >= start_of_week]
        return jsonify({"status": "success", "overall": overall, "week": process_subset(week_logs)}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

# --- 🤖 THE BULLETPROOF CHAT ENGINE ---
@app.route('/chat', methods=['POST'])
def chat():
    try:
        # Re-init if connection dropped
        if not chat_logs_ws: init_sheets()
            
        user_msg = request.json.get('message')
        
        # 1. Fetch Context
        timetable = timetable_ws.get_all_records()[-15:]
        raw_history = chat_logs_ws.get_all_records()
        memory = raw_history[-8:] # Keep history clean

        # 2. Setup System Instruction
        sys_instr = (
            f"You are 'Routine Flow Architect' for Sriniket. He is recovering from an injury. "
            f"CURRENT TIMETABLE: {json.dumps(timetable)}. "
            f"RULES: 1. Prioritize rest. 2. To suggest changes, MUST use ACTION_RECS: "
            f"{{\"action_target\": \"...\", \"new_val\": \"...\", \"reason\": \"...\"}}"
        )
        
        # 3. Model Initialization (Using Gemini 1.5 Flash)
        model = genai.GenerativeModel(
            model_name='gemini-1.5-flash',
            system_instruction=sys_instr
        )

        # 4. Hardened Role Alternation Logic
        # Gemini history MUST start with 'user' and alternate 'user' -> 'model'
        history = []
        for m in memory:
            role = "user" if m['Role'].lower() == 'user' else "model"
            # Prevent consecutive identical roles
            if history and history[-1]['role'] == role:
                continue
            history.append({"role": role, "parts": [m['Message']]})

        # Ensure history doesn't end with 'user' (because the next send_message is 'user')
        if history and history[-1]['role'] == "user":
            history.append({"role": "model", "parts": ["Understood, standing by for your next update."]})

        # 5. Execute Chat
        chat_session = model.start_chat(history=history)
        response = chat_session.send_message(user_msg)
        ai_text = response.text

        # 6. Save to Sheets
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        chat_logs_ws.append_rows([[ts, "User", user_msg], [ts, "AI", ai_text]])

        return jsonify({"status": "success", "text": ai_text}), 200

    except Exception as e:
        app.logger.error(f"V5.4.5 CRASH: {str(e)}")
        return jsonify({"status": "error", "message": f"QA_DEBUG: {str(e)}"}), 500

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
        if len(records) > 1:
            chat_logs_ws.delete_rows(2, len(records))
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