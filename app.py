from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
import os
import google.generativeai as genai
import json
import pytz  # For the Asia/Kolkata timezone fix

app = Flask(__name__)
CORS(app)

# --- 🌍 TIMEZONE SETUP ---
IST = pytz.timezone('Asia/Kolkata')

# --- 🧠 AI SETUP ---
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-3-flash-preview')

# --- 📊 GOOGLE SHEETS SETUP ---
try:
    creds_path = 'credentials.json'
    if os.path.exists(creds_path):
        client_gs = gspread.service_account(filename=creds_path)
    else:
        # Fallback for Render using Environment Variables
        creds_json = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        if creds_json:
            creds_dict = json.loads(creds_json)
            client_gs = gspread.service_account_from_dict(creds_dict)
    
    sheet = client_gs.open("overall_db")
    timetable_ws = sheet.worksheet("Timetable")
    logs_ws = sheet.worksheet("Logs")
    print("✅ Backend Status: Connected & Timezone Synced (IST)")
except Exception as e:
    print(f"❌ Connection Error: {e}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "Sriniket's ADFS Backend is Live!"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    """Fetches routine, skipping Row 1 Title and using Row 2 as Headers."""
    try:
        all_values = timetable_ws.get_all_values()
        if len(all_values) < 2:
            return jsonify({"status": "error", "message": "Sheet is empty"}), 400
            
        # Use Row 2 (index 1) as headers: 'Day', 'Time', 'Activity', 'Duration'
        headers = [h.strip() for h in all_values[1]] 
        data_rows = all_values[2:]
        
        clean_records = []
        for row in data_rows:
            if any(row): 
                record = dict(zip(headers, row))
                # Remove empty keys from ghost columns
                clean_record = {k: v for k, v in record.items() if k != ''}
                clean_records.append(clean_record)
        
        return jsonify({"status": "success", "data": clean_records, "timezone": "Asia/Kolkata"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        data = request.json
        # Log time in India Standard Time
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        
        row = [
            now_ist,
            data.get('activity', 'Unknown'),
            data.get('planned_duration', '0'),
            data.get('actual_duration', '0'),
            data.get('time_debt', 0)
        ]
        logs_ws.append_row(row)
        return jsonify({"status": "success", "message": "Logged successfully!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)