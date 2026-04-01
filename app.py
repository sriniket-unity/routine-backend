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
CORS(app) # Unlocks the gate for your GitHub website

# --- 🌍 TIMEZONE SETUP ---
IST = pytz.timezone('Asia/Kolkata')

# --- 🧠 AI SETUP ---
# Powered by Gemini 3 Flash Preview (2026 Architecture)
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-3-flash-preview')

# --- 📊 GOOGLE SHEETS SETUP ---
# We define these as None globally so the endpoints don't crash if connection fails
timetable_ws = None
logs_ws = None

try:
    # Path Priority: 1. Local 2. Render Secret 3. Env Var
    local_creds = 'credentials.json'
    render_creds = '/etc/secrets/credentials.json'
    
    client_gs = None

    if os.path.exists(local_creds):
        client_gs = gspread.service_account(filename=local_creds)
        print("✅ Connection: Local credentials.json found.")
    elif os.path.exists(render_creds):
        client_gs = gspread.service_account(filename=render_creds)
        print("✅ Connection: Render Secret File found.")
    else:
        env_creds = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        if env_creds:
            client_gs = gspread.service_account_from_dict(json.loads(env_creds))
            print("✅ Connection: Environment Variable found.")

    if client_gs:
        sheet = client_gs.open("overall_db")
        timetable_ws = sheet.worksheet("Timetable")
        logs_ws = sheet.worksheet("Logs")
        print("✅ Google Sheets: Linked & Active.")
    else:
        print("❌ Critical: No Google Credentials detected.")

except Exception as e:
    print(f"❌ Initialization Error: {e}")

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "Sriniket's ADFS Backend (Gemini 3 Flash) is Live!"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    """Fetches routine, skipping Row 1 Title and using Row 2 as Headers."""
    try:
        if not timetable_ws:
            return jsonify({"status": "error", "message": "Sheet not connected"}), 500
            
        all_values = timetable_ws.get_all_values()
        if len(all_values) < 2:
            return jsonify({"status": "error", "message": "Sheet is empty"}), 400
            
        # Row 2 (index 1) contains 'Day', 'Time', 'Activity', 'Duration'
        headers = [h.strip() for h in all_values[1]] 
        data_rows = all_values[2:]
        
        clean_records = []
        for row in data_rows:
            if any(row): 
                record = dict(zip(headers, row))
                # Remove empty keys from merged/ghost columns
                clean_record = {k: v for k, v in record.items() if k != ''}
                clean_records.append(clean_record)
        
        return jsonify({"status": "success", "data": clean_records}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        if not logs_ws: return jsonify({"status": "error"}), 500
        data = request.json
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        
        row = [
            now_ist,
            data.get('activity', 'Unknown'),
            data.get('planned_duration', '0'),
            data.get('actual_duration', '0'),
            data.get('time_debt', 0)
        ]
        logs_ws.append_row(row)
        return jsonify({"status": "success", "message": "Logged to Sheets!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/analyze_patterns', methods=['GET'])
def analyze_patterns():
    """Uses Gemini 3 Flash to optimize Sriniket's schedule."""
    try:
        logs = logs_ws.get_all_values()[-15:]
        timetable = timetable_ws.get_all_values()[1:10]
        
        prompt = f"User: Sriniket. Timetable: {timetable}. Logs: {logs}. Suggest ONE fix for Time Debt in raw JSON format."
        
        response = model.generate_content(prompt)
        raw_text = response.text.strip().replace('```json', '').replace('```', '')
        ai_data = json.loads(raw_text)
        
        return jsonify({"status": "success", "analysis": ai_data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)