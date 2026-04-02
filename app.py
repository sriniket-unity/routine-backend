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

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def health():
    status = "Ready" if logs_ws else "Error"
    return jsonify({"service": "Routine Flow Backend", "version": "4.1", "sheets": status}), 200

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
    """Logs a single session. Units: Decimal Hours."""
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
    """Batch processing for test data. Units: Decimal Hours."""
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
    """AI Analysis updated for V4.1 to prioritize Hours."""
    try:
        recs = logs_ws.get_all_records()
        if len(recs) < 3: 
            return jsonify({"status": "success", "analysis": None}), 200
        
        log_context = json.dumps(recs[-10:])
        # QA NOTE: The prompt now explicitly forces Gemini to think in HOURS.
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
        return jsonify({"status": "success", "analysis": json.loads(clean_text)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    try:
        data = request.json
        activity = data.get('activity')
        new_val = data.get('new_val')
        cell = timetable_ws.find(activity)
        if cell:
            timetable_ws.update_cell(cell.row, cell.col + 1, new_val)
            return jsonify({"status": "success", "message": f"Updated {activity}"}), 200
        return jsonify({"status": "error", "message": "Activity not found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    try:
        records = logs_ws.get_all_values()
        if len(records) > 1:
            logs_ws.delete_rows(2, len(records))
            return jsonify({"status": "success"}), 200
        return jsonify({"status": "success", "message": "Sheet already empty"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)