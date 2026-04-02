from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
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

# --- ⚡ V4.5 CACHE LAYER ---
# Stored in server memory to prevent redundant AI calls
analysis_cache = {
    "data": None,
    "log_count": 0
}

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def health():
    status = "Ready" if logs_ws else "Error"
    return jsonify({
        "service": "Routine Flow Backend",
        "version": "4.5 (Cached)",
        "sheets": status
    }), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        all_val = timetable_ws.get_all_values()
        # Row 2 contains headers
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
        logs_ws.append_row([
            ts, 
            d.get('activity'), 
            d.get('planned_duration'), 
            d.get('actual_duration'), 
            d.get('time_debt', 0)
        ])
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/bulk_log', methods=['POST'])
def bulk_log():
    try:
        data_list = request.json 
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        rows_to_add = []
        for d in data_list:
            rows_to_add.append([
                ts, 
                d.get('activity'), 
                d.get('planned_duration'), 
                d.get('actual_duration'), 
                d.get('time_debt', 0)
            ])
        logs_ws.append_rows(rows_to_add)
        return jsonify({"status": "success", "count": len(rows_to_add)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/analyze_patterns', methods=['GET'])
def analyze_patterns():
    global analysis_cache
    try:
        # 1. Fetch all raw log values
        all_logs = logs_ws.get_all_values()
        current_count = len(all_logs)

        # 2. CACHE HIT: If count is same, skip Gemini and return cached data
        if analysis_cache["data"] and current_count == analysis_cache["log_count"]:
            return jsonify({
                "status": "success", 
                "analysis": analysis_cache["data"],
                "source": "cache"
            }), 200
        
        # 3. If logs are too few (Header row + Title row + <3 data rows), return None
        if current_count < 5: 
            return jsonify({"status": "success", "analysis": None}), 200
        
        # 4. proceed with fresh Gemini analysis
        # Using the last 10 rows for context
        headers = all_logs[1] 
        recent_rows = all_logs[-10:]
        recs = [dict(zip(headers, row)) for row in recent_rows]
        
        log_context = json.dumps(recs)
        prompt = f"""
        Analyze these routine logs for Sriniket: {log_context}. 
        IMPORTANT: All durations (planned, actual, and debt) are in DECIMAL HOURS.
        Identify ONE performance trend or optimization. 
        Return ONLY a JSON object:
        {{
            "title": "Insight Title",
            "message": "Specific advice based on hour-logs",
            "action_target": "Activity Name",
            "new_val": "Suggested duration (e.g., 1.0h)"
        }}
        """
        response = model.generate_content(prompt)
        clean_text = response.text.strip().replace("```json", "").replace("```", "")
        analysis_data = json.loads(clean_text)

        # 5. UPDATE CACHE
        analysis_cache["data"] = analysis_data
        analysis_cache["log_count"] = current_count

        return jsonify({
            "status": "success", 
            "analysis": analysis_data,
            "source": "gemini_api"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    try:
        data = request.json
        activity = data.get('activity')
        new_val = data.get('new_val')
        
        # Case-Insensitive Regex Search
        pattern = re.compile(rf'^{re.escape(activity)}$', re.IGNORECASE)
        cell = timetable_ws.find(pattern)
        
        if cell:
            timetable_ws.update_cell(cell.row, cell.col + 1, new_val)
            return jsonify({"status": "success", "message": f"Updated {activity}"}), 200
        
        return jsonify({"status": "error", "message": f"Activity '{activity}' not found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    global analysis_cache
    try:
        records = logs_ws.get_all_values()
        if len(records) > 1:
            logs_ws.delete_rows(2, len(records))
            # Clear cache so engine doesn't show old data for empty sheet
            analysis_cache = {"data": None, "log_count": 0}
            return jsonify({"status": "success"}), 200
        return jsonify({"status": "success", "message": "Sheet already empty"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)