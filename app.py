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

app = Flask(__name__)
CORS(app)

# --- 🌍 CONFIGURATION ---
IST = pytz.timezone('Asia/Kolkata')
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-3-flash-preview')

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
            print("✅ Sheets Status: Timetable & Logs connected.")
    except Exception as e:
        print(f"❌ Sheets Error: {e}")

init_sheets()

# --- ⚡ CACHE LAYER ---
analysis_cache = {"data": None, "log_count": 0}

# --- 🛠️ HELPER: TIMESTAMP SANITIZER ---
def sanitize_ts(ts_str):
    """Handles cases like '12:0' by padding to '12:00'"""
    try:
        parts = ts_str.split(' ')
        date_part = parts[0]
        time_part = parts[1]
        h, m = time_part.split(':')
        return f"{date_part} {h.zfill(2)}:{m.zfill(2)}"
    except:
        return ts_str

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def health():
    return jsonify({"service": "Routine Flow Backend", "version": "4.6.4", "status": "Ready"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        all_val = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_val[1]] 
        data = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        d = request.json
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        # Matching your sheet headers exactly
        logs_ws.append_row([ts, d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)])
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    try:
        all_logs = logs_ws.get_all_records()
        if not all_logs:
            return jsonify({"status": "success", "overall": None, "week": None}), 200

        now = datetime.now(IST)
        start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

        def process_subset(subset):
            if not subset: return {"study": 0, "adherence": 0, "debt": 0, "chart": [0]*7}
            
            # --- 🛠️ V4.6.4 FIX: Match EXACT Sheet Headers ---
            total_study = sum(float(r.get('Actual (hrs)') or 0) for r in subset)
            total_debt = sum(float(r.get('Time Debt') or 0) for r in subset)
            
            completed = sum(1 for r in subset if float(r.get('Actual (hrs)') or 0) > 0)
            adherence = round((completed / len(subset)) * 100)
            
            chart = [0.0] * 7
            for r in subset:
                try:
                    clean_ts = sanitize_ts(r.get('Timestamp', ''))
                    dt = datetime.strptime(clean_ts, '%Y-%m-%d %H:%M')
                    chart[dt.weekday()] += float(r.get('Actual (hrs)') or 0)
                except: continue
            
            return {"study": round(total_study, 1), "adherence": adherence, "debt": round(total_debt, 1), "chart": chart}

        overall_data = process_subset(all_logs)
        week_logs = []
        for r in all_logs:
            try:
                clean_ts = sanitize_ts(r.get('Timestamp', ''))
                log_dt = IST.localize(datetime.strptime(clean_ts, '%Y-%m-%d %H:%M'))
                if log_dt >= start_of_week: week_logs.append(r)
            except: continue
        week_data = process_subset(week_logs)

        return jsonify({"status": "success", "overall": overall_data, "week": week_data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/analyze_patterns', methods=['GET'])
def analyze_patterns():
    global analysis_cache
    try:
        all_logs = logs_ws.get_all_values()
        current_count = len(all_logs)
        if analysis_cache["data"] and current_count == analysis_cache["log_count"]:
            return jsonify({"status": "success", "analysis": analysis_cache["data"], "source": "cache"}), 200
        if current_count < 5: 
            return jsonify({"status": "success", "analysis": None}), 200
        headers = all_logs[1] 
        recs = [dict(zip(headers, row)) for row in all_logs[-10:]]
        prompt = f"Analyze these logs for Sriniket: {json.dumps(recs)}. dur in DECIMAL HRS. Identify ONE trend. Return ONLY JSON: {{\"title\":\"...\",\"message\":\"...\",\"action_target\":\"...\",\"new_val\":\"...\"}}"
        response = model.generate_content(prompt)
        analysis_data = json.loads(response.text.strip().replace("```json", "").replace("```", ""))
        analysis_cache = {"data": analysis_data, "log_count": current_count}
        return jsonify({"status": "success", "analysis": analysis_data}), 200
    except Exception as e:
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
        return jsonify({"status": "error", "message": "Not found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    global analysis_cache
    try:
        records = logs_ws.get_all_values()
        if len(records) > 1:
            logs_ws.delete_rows(2, len(records))
            analysis_cache = {"data": None, "log_count": 0}
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)