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
        creds = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        if creds:
            client = gspread.service_account_from_dict(json.loads(creds))
            sheet = client.open("overall_db")
            timetable_ws = sheet.worksheet("Timetable")
            logs_ws = sheet.worksheet("Logs")
            print("✅ Status: Google Sheets Linked.")
    except Exception as e:
        print(f"❌ Connection Error: {e}")

init_sheets()

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "Online", "sheets": "Ready" if logs_ws else "Error"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        all_val = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_val[1]] # Row 2
        data = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        d = request.json
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        # Row format: Timestamp, Activity, Planned, Actual, Debt
        logs_ws.append_row([ts, d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)])
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/analyze_patterns', methods=['GET'])
def analyze_patterns():
    try:
        if not logs_ws: return jsonify({"status": "error", "message": "No Logs Link"}), 500
        recs = logs_ws.get_all_records()
        
        if len(recs) < 3:
            return jsonify({"status": "success", "analysis": None}), 200
        
        prompt = f"Analyze these routine logs for Sriniket: {json.dumps(recs[-10:])}. Provide ONE performance insight and a suggested duration change. Return ONLY JSON: {{\"title\":\"...\",\"message\":\"...\",\"action_target\":\"...\",\"new_val\":\"...\"}}"
        
        response = model.generate_content(prompt)
        # QA Logic: Strip any AI conversational text to get clean JSON
        clean_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify({"status": "success", "analysis": json.loads(clean_text)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))