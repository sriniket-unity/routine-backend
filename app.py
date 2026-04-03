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
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# --- 📊 SHEETS CONNECTION ---
timetable_ws = None
logs_ws = None

def init_sheets():
    global timetable_ws, logs_ws
    try:
        creds_json = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        if creds_json:
            client = gspread.service_account_from_dict(json.loads(creds_json))
            sheet = client.open("overall_db")
            timetable_ws = sheet.worksheet("Timetable")
            logs_ws = sheet.worksheet("Logs")
            return True
    except: return False

init_sheets()

# --- 🛠️ ULTRA-ROBUST TIME PARSER ---
def parse_time_to_minutes(t_str):
    try:
        # Clean string: " 08 : 30 AM " -> "08:30AM"
        clean = re.sub(r'\s+', '', t_str.strip().upper())
        match = re.match(r"(\d+):(\d+)(AM|PM)?", clean)
        if not match: return 0
        h, m, mod = match.groups()
        h, m = int(h), int(m)
        if h == 12: h = 0
        if mod == "PM": h += 12
        return h * 60 + m
    except: return 0

# --- ☁️ CLOUD SYNC STATE ---
cloud_state = {"state": "READY", "activity": None, "start_time": None, "accumulated_seconds": 0}

@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "Online", "version": "5.6.6"}), 200

@app.route('/get_dashboard', methods=['GET'])
def get_dashboard():
    try:
        if not timetable_ws: init_sheets()
        all_tt = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_tt[1] if h.strip()] 
        timetable_data = [dict(zip(headers, r)) for r in all_tt[2:] if any(r)]
        
        now = datetime.now(IST)
        curMin = (now.hour * 60) + now.minute
        
        cur_session = None
        for item in timetable_data:
            times = item.get('Time', '').split('-')
            if len(times) != 2: continue
            s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
            if (e < s and (curMin >= s or curMin < e)) or (s <= curMin < e):
                cur_session = item; break
            
        if not cur_session:
            return jsonify({"status": "success", "cur": {"Activity": "BREAK", "Duration": "1"}, "prev": {"Activity": "---"}, "next": {"Activity": "---"}})
        
        idx = timetable_data.index(cur_session)
        return jsonify({
            "status": "success",
            "prev": timetable_data[idx-1] if idx > 0 else timetable_data[-1],
            "cur": cur_session,
            "next": timetable_data[idx+1] if idx < len(timetable_data)-1 else timetable_data[0]
        })
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_state', methods=['GET'])
def get_state(): return jsonify({"status": "success", "data": cloud_state})

@app.route('/set_state', methods=['POST'])
def set_state():
    global cloud_state
    cloud_state.update(request.json)
    return jsonify({"status": "success"})

@app.route('/chat', methods=['POST'])
def chat():
    try:
        model = genai.GenerativeModel('gemini-3-flash-preview')
        res = model.generate_content(f"Sriniket: {request.json.get('message')}")
        return jsonify({"status": "success", "text": res.text})
    except: return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))