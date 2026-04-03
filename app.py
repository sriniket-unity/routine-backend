# start of version v5.8.5
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
import threading

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
            return True
        else:
            app.logger.error("❌ CRITICAL: GOOGLE_SHEETS_CREDS_JSON is missing.")
            return False
    except Exception as e:
        app.logger.error(f"❌ Sheets Error: {e}")
        return False

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
        clean = re.sub(r'\s+', '', t_str.strip().upper())
        match = re.match(r"(\d+):(\d+)(AM|PM)?", clean)
        if not match: return 0
        h, m, mod = match.groups()
        h, m = int(h), int(m)
        if h == 12: h = 0
        if mod == "PM": h += 12
        return h * 60 + m
    except: return 0

def safe_float(val):
    try:
        if not val or str(val).strip() == '': return 0.0
        return float(val)
    except: return 0.0

# --- ☁️ CLOUD SYNC STATE ---
cloud_state = {
    "state": "READY",
    "activity": None,
    "start_time": None,
    "accumulated_seconds": 0 
}

# --- 🌐 ENDPOINTS ---
@app.route('/', methods=['GET'])
def health():
    return jsonify({
        "service": "Routine Flow Architect", 
        "version": "5.8.5", 
        "status": "Online",
        "model": "gemini-3-flash-preview"
    }), 200

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
    cloud_state["accumulated_seconds"] = data.get("accumulated_seconds", 0) or 0
    return jsonify({"status": "success"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        if not timetable_ws: init_sheets()
        if not timetable_ws: return jsonify({"status": "error", "message": "DB ERROR"}), 500
        all_val = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_val[1] if h.strip()] 
        data = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        now = datetime.now(IST)
        curMin = (now.hour * 60) + now.minute
        cur_session = None
        for item in data:
            times = item.get('Time', '').split('-')
            if len(times) != 2: continue
            s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
            if (e < s and (curMin >= s or curMin < e)) or (s <= curMin < e):
                cur_session = item; break
        if not cur_session:
            return jsonify({"status": "success", "data": data, "cur": {"Activity": "BREAK", "Duration": "1"}, "prev": {"Activity": "---"}, "next": {"Activity": "---"}})
        idx = data.index(cur_session)
        return jsonify({
            "status": "success",
            "data": data,
            "prev": data[idx-1] if idx > 0 else data[-1],
            "cur": cur_session,
            "next": data[idx+1] if idx < len(data)-1 else data[0]
        })
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    try:
        if not logs_ws: init_sheets()
        if not logs_ws: return jsonify({"status": "error"}), 500
        raw_logs = logs_ws.get_all_values()
        if len(raw_logs) <= 1: return jsonify({"status": "success", "overall": None, "week": None}), 200
        headers = [h.strip() for h in raw_logs[0] if h.strip()]
        all_logs = [dict(zip(headers, r)) for r in raw_logs[1:] if any(r)]
        now = datetime.now(IST)
        start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)

        def process_subset(subset):
            if not subset: return {"study": 0, "adherence": 0, "debt": 0, "chart": [0.0]*7}
            keys = list(subset[0].keys()) if subset else []
            act_k = next((k for k in keys if 'actual' in k.lower()), None)
            debt_k = next((k for k in keys if 'debt' in k.lower()), None)
            ts_k = next((k for k in keys if 'time' in k.lower() or 'stamp' in k.lower()), None)
            valid_rows = [r for r in subset if str(r.get(act_k, '')).strip() or str(r.get(debt_k, '')).strip()]
            if not valid_rows: return {"study": 0, "adherence": 0, "debt": 0, "chart": [0.0]*7}
            total_study = sum(safe_float(r.get(act_k)) for r in valid_rows)
            total_debt = sum(safe_float(r.get(debt_k)) for r in valid_rows)
            completed = sum(1 for r in valid_rows if safe_float(r.get(act_k)) > 0)
            adherence = round((completed / len(valid_rows)) * 100)
            chart = [0.0] * 7
            for r in valid_rows:
                try:
                    dt = datetime.strptime(sanitize_ts(r.get(ts_k, '')), '%Y-%m-%d %H:%M')
                    chart[dt.weekday()] += safe_float(r.get(act_k))
                except: continue
            return {"study": round(total_study, 1), "adherence": adherence, "debt": round(total_debt, 1), "chart": chart}

        week_logs = []
        ts_key = next((k for k in all_logs[0].keys() if 'time' in k.lower() or 'stamp' in k.lower()), None)
        for r in all_logs:
            try:
                if IST.localize(datetime.strptime(sanitize_ts(r.get(ts_key, '')), '%Y-%m-%d %H:%M')) >= start_of_week:
                    week_logs.append(r)
            except: continue
        return jsonify({
            "status": "success", 
            "overall": process_subset(all_logs), 
            "week": process_subset(week_logs)
        }), 200
    except Exception as e: 
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500

def save_chat_bg(timestamp, user_message, ai_message):
    try:
        if chat_logs_ws:
            chat_logs_ws.append_rows([[timestamp, "User", user_message], [timestamp, "AI", ai_message]])
    except Exception as e:
        app.logger.error(f"Background chat save failed: {e}")

@app.route('/chat', methods=['POST'])
def chat():
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
        System: You are 'Routine Flow Architect', an AI assistant for Sriniket.
        Context: Sriniket is recovering from a bike accident.
        Schedule Context: {json.dumps(lean_tt)}
        Memory: {json.dumps(memory)}
        
        CRITICAL INSTRUCTION: 
        1. If the user asks a direct/simple question (e.g., "what model are you?", "what is 2+2?"), answer it DIRECTLY and CONCISELY. Do NOT add unsolicited advice about their schedule or recovery.
        2. ONLY provide schedule recommendations or mention the recovery context IF the user explicitly asks for advice, schedule changes, or discusses their health/routine.
        
        User Input: {user_msg}
        
        Mandatory Change Format (Use ONLY if making schedule changes):
        ACTION_RECS: {{"action_target": "Activity Name", "new_val": "0.5h", "reason": "Rest and recovery"}}
        """
        model = genai.GenerativeModel('gemini-3-flash-preview')
        response = model.generate_content(prompt)
        
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        threading.Thread(target=save_chat_bg, args=(ts, user_msg, response.text)).start()
        
        return jsonify({"status": "success", "text": response.text}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        d = request.json
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        logs_ws.append_row([ts, d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)])
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/bulk_log', methods=['POST'])
def bulk_log():
    try:
        if not logs_ws: init_sheets()
        data = request.json
        rows = [[datetime.now(IST).strftime('%Y-%m-%d %H:%M'), d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)] for d in data]
        logs_ws.append_rows(rows)
        return jsonify({"status": "success", "inserted": len(rows)}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/clear_chat', methods=['DELETE'])
def clear_chat():
    try:
        records = chat_logs_ws.get_all_values()
        if len(records) > 1: chat_logs_ws.delete_rows(2, len(records))
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    try:
        if not logs_ws: init_sheets()
        records = logs_ws.get_all_values()
        if len(records) > 1:
            logs_ws.delete_rows(2, len(records))
        return jsonify({"status": "success"}), 200
    except Exception as e: 
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500

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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
# end of version v5.8.5