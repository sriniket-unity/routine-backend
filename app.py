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
import logging

app = Flask(__name__)
CORS(app)

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
            app.logger.info("✅ Sheets Status: Gemini Systems Synchronized.")
    except Exception as e:
        app.logger.error(f"❌ Sheets Error: {e}")

init_sheets()

# --- 🛠️ HELPERS ---
def sanitize_ts(ts_str):
    try:
        parts = ts_str.split(' ')
        h, m = parts[1].split(':')
        return f"{parts[0]} {h.zfill(2)}:{m.zfill(2)}"
    except: return ts_str

def parse_time_to_minutes(t_str):
    try:
        parts = t_str.split(' ')
        time_parts = parts[0].split(':')
        h = int(time_parts[0])
        m = int(time_parts[1])
        mod = parts[1].strip().upper()
        if h == 12: h = 0
        minutes = h * 60 + m
        if mod == 'PM': minutes += 12 * 60
        return minutes
    except Exception as e: return 0

# --- ☁️ CLOUD SYNC STATE V5.6.1 (UPDATED) ---
cloud_state = {
    "state": "READY",
    "activity": None,
    "start_time": None,
    "accumulated_seconds": 0  # To prevent time-jumping when paused
}

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def health():
    return jsonify({
        "service": "Routine Flow Architect", 
        "version": "5.6.1", 
        "status": "Online",
        "model": "gemini-3-flash-preview"
    }), 200

# V5.6.1: NEW SINGLE-RESPONSIBILITY ENDPOINT FOR THE DASHBOARD
@app.route('/get_dashboard', methods=['GET'])
def get_dashboard():
    try:
        if not timetable_ws: init_sheets()
        all_tt = timetable_ws.get_all_values()
        # Resilient Header Parsing
        headers = [h.strip() for h in all_tt[1] if h.strip()] 
        timetable_data = [dict(zip(headers, r)) for r in all_tt[2:] if any(r)]
        
        now = datetime.now(IST)
        curMin = (now.getHours() * 60) + now.getMinutes()
        
        def is_current_session(item):
            try:
                time_range = item.get('Time', '').split('-')
                start_min = parse_time_to_minutes(time_range[0].strip())
                end_min = parse_time_to_minutes(time_range[1].strip())
                # Handle end minute wrapping past midnight
                if end_min < start_min: 
                    return curMin >= start_min or curMin < end_min
                return curMin >= start_min and curMin < end_min
            except: return False
            
        # 🕵️‍♂️ HARDENED SESSION FINDER
        cur_session = next((item for item in timetable_data if is_current_session(item)), None)
        
        # If no session found, default to a BREAK state
        if not cur_session: return jsonify({
            "status": "success",
            "prev": timetable_data[-1] if timetable_data else None,
            "cur": {"Activity": "BREAK", "Duration": "1", "Time": "00:00 - 00:00"},
            "next": timetable_data[0] if timetable_data else None
        })
        
        idx = timetable_data.index(cur_session)
        prev_session = timetable_data[idx - 1] if idx > 0 else timetable_data[-1]
        next_session = timetable_data[idx + 1] if idx < len(timetable_data) - 1 else timetable_data[0]

        return jsonify({
            "status": "success",
            "prev": prev_session,
            "cur": cur_session,
            "next": next_session
        })
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_state', methods=['GET'])
def get_state():
    return jsonify({"status": "success", "data": cloud_state}), 200

@app.route('/set_state', methods=['POST'])
def set_state():
    global cloud_state
    data = request.json
    cloud_state["state"] = data.get("state", "READY")
    cloud_state["activity"] = data.get("activity")
    cloud_state["start_time"] = data.get("start_time")
    # Store accumulated time safely, default to 0
    cloud_state["accumulated_seconds"] = int(data.get("accumulated_seconds", 0) or 0)
    return jsonify({"status": "success"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    # Existing resilient gspread logic...
    try:
        if not timetable_ws: init_sheets()
        all_val = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_val[1] if h.strip()] 
        data = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        return jsonify({"status": "success", "data": data})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    # Existing resilient analytics logic...
    try:
        if not logs_ws: init_sheets()
        raw_logs = logs_ws.get_all_values()
        if len(raw_logs) <= 1: return jsonify({"status": "success", "overall": None, "week": None}), 200
        headers = [h.strip() for h in raw_logs[0] if h.strip()]
        all_logs = [dict(zip(headers, r)) for r in raw_logs[1:] if any(r)]
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
        return jsonify({"status": "success", "overall": process_subset(all_logs), "week": process_subset([r for r in all_logs if IST.localize(datetime.strptime(sanitize_ts(r.get('Timestamp', '')), '%Y-%m-%d %H:%M')) >= start_of_week])}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/chat', methods=['POST'])
def chat():
    # Existing chat logic...
    try:
        if not chat_logs_ws: init_sheets()
        user_msg = request.json.get('message')
        all_tt = timetable_ws.get_all_values()
        tt_headers = [h.strip() for h in all_tt[1] if h.strip()]
        timetable_data = [dict(zip(tt_headers, r)) for r in all_tt[2:] if any(r)]
        lean_tt = timetable_data[-10:]
        all_chat = chat_logs_ws.get_all_values()
        memory = []
        if len(all_chat) > 1:
            chat_headers = [h.strip() for h in all_chat[0] if h.strip()]
            memory = [dict(zip(chat_headers, r)) for r in all_chat[1:] if any(r)][-6:]
        prompt = f"""
        System: You are 'Routine Flow Architect' for Sriniket. Sriniket is recovering from a bike accident.
        Schedule: {json.dumps(lean_tt)}
        Memory: {json.dumps(memory)}
        User Input: {user_msg}
        Mandatory Change Format:
        ACTION_RECS: {{"action_target": "Activity Name", "new_val": "0.5h", "reason": "Rest and recovery"}}
        """
        model = genai.GenerativeModel('gemini-3-flash-preview')
        response = model.generate_content(prompt)
        ai_text = response.text
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        chat_logs_ws.append_rows([[ts, "User", user_msg], [ts, "AI", ai_text]])
        return jsonify({"status": "success", "text": ai_text}), 200
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": f"QA_DEBUG: {str(e)}"}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    # Existing resilient logging logic...
    try:
        d = request.json
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        logs_ws.append_row([ts, d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)])
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/clear_chat', methods=['DELETE'])
def clear_chat():
    # Existing chat clearing logic...
    try:
        records = chat_logs_ws.get_all_values()
        if len(records) > 1:
            chat_logs_ws.delete_rows(2, len(records))
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    # Existing timetable updating logic...
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