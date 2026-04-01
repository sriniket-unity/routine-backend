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

# --- 🌍 TIMEZONE SETUP ---
IST = pytz.timezone('Asia/Kolkata')

# --- 🧠 AI SETUP ---
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-3-flash-preview')

# --- 📊 GOOGLE SHEETS SETUP ---
timetable_ws = None
logs_ws = None

def init_sheets():
    global timetable_ws, logs_ws
    try:
        print("🔍 Starting Google Sheets Connection Sequence...")
        
        # 1. Check for Environment Variable (Render Top Box)
        env_creds = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        
        # 2. Check for Secret File (Render Bottom Box)
        render_secret_path = '/etc/secrets/credentials.json'
        
        # 3. Check for Local File (Your Laptop)
        local_creds = 'credentials.json'

        client_gs = None

        if env_creds:
            print("💡 Attempting connection via Environment Variable...")
            client_gs = gspread.service_account_from_dict(json.loads(env_creds))
        elif os.path.exists(render_creds_path := render_secret_path):
            print(f"💡 Attempting connection via Render Secret File: {render_creds_path}")
            client_gs = gspread.service_account(filename=render_creds_path)
        elif os.path.exists(local_creds):
            print("💡 Attempting connection via Local credentials.json...")
            client_gs = gspread.service_account(filename=local_creds)
        
        if client_gs:
            print("✅ Step 1/2: Google Authentication Successful.")
            # Ensure the sheet name "overall_db" matches your Google Sheet exactly!
            sheet = client_gs.open("overall_db")
            timetable_ws = sheet.worksheet("Timetable")
            logs_ws = sheet.worksheet("Logs")
            print("✅ Step 2/2: Connected to 'overall_db' and found worksheets.")
        else:
            print("❌ Step 1/2 Failed: No credentials found in Env Vars or Files.")

    except Exception as e:
        print(f"❌ CRITICAL CONNECTION ERROR: {e}")

# Run the initialization
init_sheets()

# --- 🌐 ENDPOINTS ---

@app.route('/', methods=['GET'])
def home():
    connection_status = "Connected ✅" if timetable_ws else "Disconnected ❌"
    return jsonify({
        "message": "Sriniket's ADFS Backend is Live!",
        "sheets_status": connection_status
    }), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        if not timetable_ws:
            # Try to re-init if it failed once
            init_sheets()
            if not timetable_ws:
                return jsonify({"status": "error", "message": "Sheet not connected. Check Render Logs for the specific error."}), 500
            
        all_values = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_values[1]] # Row 2
        data_rows = all_values[2:] # Row 3 onwards
        
        clean_records = []
        for row in data_rows:
            if any(row): 
                record = dict(zip(headers, row))
                clean_record = {k: v for k, v in record.items() if k != ''}
                clean_records.append(clean_record)
        
        return jsonify({"status": "success", "data": clean_records}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# (Keep your /log_session and /analyze_patterns as they were)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)