from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
import os
import google.generativeai as genai
import json

app = Flask(__name__)
# IMPORTANT: This allows your GitHub Pages site to talk to this Render backend
CORS(app)

# --- 🧠 AI SETUP ---
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-3-flash-preview')

# --- 📊 GOOGLE SHEETS SETUP ---
try:
    # This logic works for both local (file) and Render (Env Var)
    creds_path = 'credentials.json'
    if os.path.exists(creds_path):
        client_gs = gspread.service_account(filename=creds_path)
    else:
        # For professional deployment, we'd use an Env Var for the JSON content
        creds_dict = json.loads(os.environ.get("GOOGLE_SHEETS_CREDS_JSON"))
        client_gs = gspread.service_account_from_dict(creds_dict)
    
    sheet = client_gs.open("overall_db")
    timetable_ws = sheet.worksheet("Timetable")
    logs_ws = sheet.worksheet("Logs")
    print("✅ Backend Status: Connected to Google Sheets & Gemini 3")
except Exception as e:
    print(f"❌ Connection Error: {e}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "Sriniket's ADFS Backend is Live!"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    """Fetches routine, skipping the Row 1 Title and using Row 2 as Headers."""
    try:
        # Fetching all values to handle the Row 1 Title offset
        all_values = timetable_ws.get_all_values()
        
        if len(all_values) < 2:
            return jsonify({"status": "error", "message": "Sheet is empty"}), 400
            
        # Row 0 is Title, Row 1 is Headers ('Day', 'Time', 'Activity', 'Duration')
        headers = all_values[1] 
        data_rows = all_values[2:]
        
        # Clean headers to remove any trailing spaces
        headers = [h.strip() for h in headers]
        
        # Convert rows to list of dictionaries, skipping empty rows
        clean_records = []
        for row in data_rows:
            if any(row): # Only add if the row isn't totally empty
                record = dict(zip(headers, row))
                # Remove any keys that are empty strings (from empty columns)
                clean_record = {k: v for k, v in record.items() if k != ''}
                clean_records.append(clean_record)
        
        return jsonify({"status": "success", "data": clean_records}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        data = request.json
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data.get('activity', 'Unknown'),
            data.get('planned_duration', '0'),
            data.get('actual_duration', '0'),
            data.get('time_debt', 0)
        ]
        logs_ws.append_row(row)
        return jsonify({"status": "success", "message": "Logged successfully!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/analyze_patterns', methods=['GET'])
def analyze_patterns():
    try:
        logs = logs_ws.get_all_values()[-20:]
        timetable = timetable_ws.get_all_values()[1:15] # Only send headers + first few rows
        
        prompt = f"""
        User: Sriniket
        Role: Performance Coach
        Context: Timetable is {timetable}. Recent logs are {logs}.
        Task: Provide ONE JSON suggestion to reduce 'Time Debt'.
        Rules: Return ONLY raw JSON. No markdown.
        Format: {{"title": "", "message": "", "action_target": "", "new_val": ""}}
        """
        response = model.generate_content(prompt)
        raw_text = response.text.strip().replace('```json', '').replace('```', '')
        ai_data = json.loads(raw_text)
        
        return jsonify({"status": "success", "analysis": ai_data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)