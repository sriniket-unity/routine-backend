from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
import os

app = Flask(__name__)
CORS(app)

# --- GOOGLE SHEETS SETUP ---
try:
    # Render will look for the 'credentials.json' file we added as a Secret File
    client = gspread.service_account(filename='credentials.json')
    
    # Opens your specific Google Sheet
    sheet = client.open("overall_db")
    
    # Connects to your two tabs
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
    """Reads the master timetable and sends it to your app."""
    try:
        # head=2 because Row 1 is your '70 Hour Plan' title
        records = timetable_ws.get_all_records(head=2)
        
        # This removes any empty columns from the sheet data
        clean_records = [{k: v for k, v in record.items() if k != ''} for record in records]
        
        return jsonify({"status": "success", "data": clean_records}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    """Receives checkout data and appends it to the Logs tab."""
    try:
        data = request.json
        # Prep the row to match your Google Sheet columns
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

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    """Deletes all entries in the Logs tab except the header row."""
    try:
        # Get all current values to determine the row count
        all_values = logs_ws.get_all_values()
        num_rows = len(all_values)

        if num_rows > 1:
            # delete_rows(start_index, end_index)
            # We start at 2 to preserve your headers in Row 1
            logs_ws.delete_rows(2, num_rows)
            return jsonify({"status": "success", "message": f"Cleared {num_rows - 1} log entries."}), 200
        else:
            return jsonify({"status": "success", "message": "Logs are already empty."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- RUN THE SERVER ---
if __name__ == '__main__':
    # Dynamic port for Render deployment
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)