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
            app.logger.info("✅ Sheets Synchronized.")
    except Exception as e:
        app.logger.error(f"❌ Sheets Error: {e}")

init_sheets()

# --- 🛠️ HELPERS ---
def sanitize_ts(ts_str):
    try:
        parts = ts_str.strip().split(' ')
        h_m = parts[1].split(':')
        return f"{parts[0]} {h_m[0].zfill(2)}:{h_m[1].zfill(2)}"
    except: return ts_str

def parse_time_to_minutes(t_str):
    try:
        t_str = t_str.strip().upper()
        # Handle formats like "08:30 AM" or "8:30 AM"
        match = re.match(r"(\d+):(\d+)\s*(AM|PM)", t_str)
        if not match: return 0
        h, m, mod = match.groups()
        h, m = int(h), int(m)
        if h == 12: h = 0
        if mod == "PM": h += 12
        return h * 60 + m
    except: return 0

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
    return jsonify({"status": "Online", "version": "5.6.5"}), 200

@app.route('/get_dashboard', methods=['GET'])
def get_dashboard():
    try:
        if not timetable_ws: init_sheets()
        all_tt = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_tt[1] if h.strip()] 
        timetable_data = [dict(zip(headers, r)) for r in all_tt[2:] if any(r)]
        
        now = datetime.now(IST)
        curMin = (now.getHours() * 60) + now.getMinutes()
        
        cur_session = None
        for item in timetable_data:
            times = item.get('Time', '').split('-')
            if len(times) != 2: continue
            s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
            if e < s: # Midnight wrap
                if curMin >= s or curMin < e: cur_session = item; break
            elif s <= curMin < e: cur_session = item; break
            
        if not cur_session:
            return jsonify({"status": "success", "prev": {"Activity": "---"}, "cur": {"Activity": "BREAK", "Duration": "1"}, "next": {"Activity": "---"}})
        
        idx = timetable_data.index(cur_session)
        return jsonify({
            "status": "success",
            "prev": timetable_data[idx-1] if idx > 0 else timetable_data[-1],
            "cur": cur_session,
            "next": timetable_data[idx+1] if idx < len(timetable_data)-1 else timetable_data[0]
        })
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_state', methods=['GET'])
def get_state():
    return jsonify({"status": "success", "data": cloud_state}), 200

@app.route('/set_state', methods=['POST'])
def set_state():
    global cloud_state
    d = request.json
    cloud_state.update({"state": d.get("state"), "activity": d.get("activity"), "start_time": d.get("start_time"), "accumulated_seconds": int(d.get("accumulated_seconds", 0))})
    return jsonify({"status": "success"}), 200

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    try:
        if not logs_ws: init_sheets()
        raw = logs_ws.get_all_values()
        if len(raw) <= 1: return jsonify({"status": "success", "overall": None, "week": None})
        
        headers = [h.strip() for h in raw[0] if h.strip()]
        logs = [dict(zip(headers, r)) for r in raw[1:] if any(r)]
        now = datetime.now(IST)
        week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0)

        def process(subset):
            if not subset: return {"study": 0, "adherence": 0, "debt": 0, "chart": [0.0]*7}
            study = sum(float(r.get('Actual (hrs)', 0) or 0) for r in subset)
            debt = sum(float(r.get('Time Debt', 0) or 0) for r in subset)
            chart = [0.0]*7
            for r in subset:
                try:
                    dt = datetime.strptime(sanitize_ts(r.get('Timestamp')), '%Y-%m-%d %H:%M')
                    chart[dt.weekday()] += float(r.get('Actual (hrs)', 0) or 0)
                except: continue
            return {"study": round(study, 1), "adherence": round((sum(1 for r in subset if float(r.get('Actual (hrs)', 0))>0)/len(subset))*100), "debt": round(debt, 1), "chart": chart}

        return jsonify({"status": "success", "overall": process(logs), "week": process([r for r in logs if IST.localize(datetime.strptime(sanitize_ts(r.get('Timestamp')), '%Y-%m-%d %H:%M')) >= week_start])})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_msg = request.json.get('message')
        model = genai.GenerativeModel('gemini-3-flash-preview')
        response = model.generate_content(f"Sriniket says: {user_msg}. Answer concisely as his Routine Architect.")
        return jsonify({"status": "success", "text": response.text})
    except: return jsonify({"status": "error"}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    d = request.json
    logs_ws.append_row([datetime.now(IST).strftime('%Y-%m-%d %H:%M'), d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt')])
    return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))