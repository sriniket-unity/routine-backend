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
# Setting the timezone to IST to ensure logs match your local time
IST = pytz.timezone('Asia/Kolkata')

# Initialize Gemini 3 Flash
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-3-flash-preview')

# --- 📊 GOOGLE SHEETS CONNECTION ---
timetable_ws = None
logs_ws = None

def init_sheets():
    global timetable_ws, logs_ws
    try:
        creds_json = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        if creds_json:
            client = gspread.service_account_from_dict(json.loads(creds_json))
            # Ensure your sheet name matches exactly: "overall_db"
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
    """Service status check for Render."""
    status = "Ready" if logs_ws else "Error"
    return jsonify({"service": "Routine Flow Backend", "sheets": status}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    """Fetches the full timetable from Google Sheets."""
    try:
        all_val = timetable_ws.get_all_values()
        # Row 2 contains headers, data starts at Row 3
        headers = [h.strip() for h in all_val[1]] 
        data = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    """Logs a completed session to the Logs tab."""
    try:
        d = request.json
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        # Format: Timestamp, Activity, Planned, Actual, Debt
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

@app.route('/analyze_patterns', methods=['GET'])
def analyze_patterns():
    """Triggers Gemini 3 Flash to analyze recent performance trends."""
    try:
        recs = logs_ws.get_all_records()
        if len(recs) < 3: 
            return jsonify({"status": "success", "analysis": None, "message": "Need more logs"}), 200
        
        # We send the last 10 logs for context
        log_context = json.dumps(recs[-10:])
        prompt = f"""
        Analyze these routine logs for Sriniket: {log_context}
        Identify ONE performance trend. 
        Return ONLY a JSON object:
        {{
            "title": "Insight Title",
            "message": "Specific advice",
            "action_target": "Activity Name",
            "new_val": "Suggested duration (e.g. 1.0h)"
        }}
        """
        response = model.generate_content(prompt)
        # Clean response to ensure valid JSON
        clean_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify({"status": "success", "analysis": json.loads(clean_text)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    """Allows the AI to modify a duration in the Timetable sheet."""
    try:
        data = request.json
        activity = data.get('activity')
        new_val = data.get('new_val')
        
        # Find the row matching the activity name
        cell = timetable_ws.find(activity)
        if cell:
            # Update the column immediately to the right (Duration)
            timetable_ws.update_cell(cell.row, cell.col + 1, new_val)
            return jsonify({"status": "success", "message": f"Updated {activity} to {new_val}"}), 200
        
        return jsonify({"status": "error", "message": "Activity not found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    """QA Utility: Wipes all log data while keeping the header row."""
    try:
        if not logs_ws:
            return jsonify({"status": "error", "message": "Logs sheet not connected"}), 500
        
        # Get all values to determine the range
        records = logs_ws.get_all_values()
        if len(records) > 1:
            # Delete rows from index 2 (second row) to the end
            logs_ws.delete_rows(2, len(records))
            return jsonify({"status": "success", "message": "Logs cleared successfully."}), 200
        else:
            return jsonify({"status": "success", "message": "Logs were already empty."}), 200
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)