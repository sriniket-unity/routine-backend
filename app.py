from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
import os

app = Flask(__name__)
CORS(app)

# --- GOOGLE SHEETS SETUP ---
try:
    # Connects using your credentials.json
    # Note: On Render, this file will be provided via the "Secret Files" setting
    client = gspread.service_account(filename='credentials.json')
    
    # Opens your sheet named "overall_db"
    sheet = client.open("overall_db")
    
    # Connects to your specific tabs (matches your screenshots exactly)
    timetable_ws = sheet.worksheet("Timetable")
    logs_ws = sheet.worksheet("Logs")
    
    print("✅ Successfully connected to Google Sheets!")
except Exception as e:
    print(f"❌ Error connecting to sheet: {e}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "DSA Timer Backend is live on Render!"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    """Reads the master timetable and sends it to your web app."""
    try:
        # head=2 tells Python your actual headers (Day, Time, Activity) are on Row 2
        records = timetable_ws.get_all_records(head=2)
        
        # Removes any empty columns to prevent "duplicate header" errors
        clean_records = [{k: v for k, v in record.items() if k != ''} for record in records]
        
        return jsonify({"status": "success", "data": clean_records}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    """Receives checkout data and appends it to the Logs tab."""
    try:
        data = request.json
        # Prep the row: Date/Time, Activity, Planned, Actual, Debt
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

# --- RUN THE SERVER ---
if __name__ == '__main__':
    # Render assigns a dynamic port, so we grab it from environment variables
    # We use host='0.0.0.0' to allow the server to accept external requests
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)