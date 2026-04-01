from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
import os
import google.generativeai as genai
import json

app = Flask(__name__)
CORS(app)

# --- 🧠 AI SETUP (Gemini 3 Flash Preview) ---
# Powered by the latest 2026 architecture
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-3-flash-preview')

# --- 📊 GOOGLE SHEETS SETUP ---
try:
    client_gs = gspread.service_account(filename='credentials.json')
    sheet = client_gs.open("overall_db")
    
    timetable_ws = sheet.worksheet("Timetable")
    logs_ws = sheet.worksheet("Logs")
    
    print("✅ Gemini 3 Flash Preview Backend: Connected & Active.")
except Exception as e:
    print(f"❌ Connection Error: {e}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "Sriniket's ADFS Backend (Gemini 3 Flash) is Live!"}), 200

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

@app.route('/analyze_patterns', methods=['GET'])
def analyze_patterns():
    """Uses Gemini 3 Flash to optimize Sriniket's schedule."""
    try:
        logs = logs_ws.get_all_values()[-20:] # AI studies the last 20 entries
        timetable = timetable_ws.get_all_values()
        
        prompt = f"""
        User: Sriniket
        Role: High-Performance DSA & Gym Coach
        Context: Timetable is {timetable}. Actual logs are {logs}.
        
        Task: Identify why Sriniket is missing his sessions or accumulating 'Time Debt'.
        Provide ONE specific, high-impact suggestion.
        
        OUTPUT RULES: Return ONLY a raw JSON object. Do not use markdown.
        Format: 
        {{
            "title": "Clear Headline",
            "message": "The reasoning behind the advice",
            "action_target": "Activity Name",
            "new_val": "Revised Duration/Time"
        }}
        """
        
        # Using Gemini 3's fast generation
        response = model.generate_content(prompt)
        
        # Surgical cleaning of the AI response
        raw_text = response.text.strip().replace('```json', '').replace('```', '')
        ai_data = json.loads(raw_text)
        
        return jsonify({"status": "success", "analysis": ai_data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    try:
        data = request.json
        activity = data.get('activity')
        new_val = data.get('new_val')
        
        cell = timetable_ws.find(activity)
        timetable_ws.update_cell(cell.row, cell.col + 1, new_val)
        
        return jsonify({"status": "success", "message": f"Synced {activity} to {new_val}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)