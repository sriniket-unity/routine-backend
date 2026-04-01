from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
import os
import google.generativeai as genai
import json

app = Flask(__name__)
CORS(app)

# --- AI SETUP ---
# This pulls your free key from Render's environment variables
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

# --- GOOGLE SHEETS SETUP ---
try:
    client_gs = gspread.service_account(filename='credentials.json')
    sheet = client_gs.open("overall_db")
    timetable_ws = sheet.worksheet("Timetable")
    logs_ws = sheet.worksheet("Logs")
    print("✅ Gemini Backend Live: Sheets Connected.")
except Exception as e:
    print(f"❌ Connection Error: {e}")

# --- CORE ROUTES ---

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        records = timetable_ws.get_all_records(head=1)
        clean_records = [{k: v for k, v in record.items() if k != ''} for record in records]
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

# --- GEMINI PATTERN ANALYZER ---

@app.route('/analyze_patterns', methods=['GET'])
def analyze_patterns():
    try:
        # 1. Fetch recent data for the AI to study
        logs = logs_ws.get_all_values()[-15:] # Last 15 entries
        timetable = timetable_ws.get_all_values()
        
        # 2. The "Opal" AI Prompt
        prompt = f"""
        You are an elite productivity coach. 
        User Timetable: {timetable}
        Recent Activity Logs: {logs}
        
        Task: Find a bottleneck (e.g., user is always late starting Study after Gym).
        Suggest ONE specific adjustment. 
        
        IMPORTANT: Return ONLY a raw JSON object with these keys:
        {{
            "title": "Short title of advice",
            "message": "Detailed explanation of the pattern seen",
            "action_target": "Exact name of activity to change",
            "new_val": "Suggested new duration or time"
        }}
        """
        
        response = model.generate_content(prompt)
        # Clean up the response text in case Gemini adds markdown backticks
        json_data = response.text.replace('```json', '').replace('```', '').strip()
        
        return jsonify({"status": "success", "analysis": json.loads(json_data)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    try:
        data = request.json
        activity = data.get('activity')
        new_val = data.get('new_val')
        
        # Find the activity in the Timetable sheet and update the duration column
        cell = timetable_ws.find(activity)
        timetable_ws.update_cell(cell.row, cell.col + 1, new_val)
        
        return jsonify({"status": "success", "message": f"Updated {activity} successfully."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)