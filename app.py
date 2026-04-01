from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
import os

app = Flask(__name__)
CORS(app)

# --- GOOGLE SHEETS SETUP ---
try:
    # Render uses the 'credentials.json' we uploaded to the 'Secret Files' section
    client = gspread.service_account(filename='credentials.json')
    
    # Opens your main database sheet
    sheet = client.open("overall_db")
    
    # Connects to your specific tabs
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
        # head=2 because your headers (Day, Time, etc.) are on Row 2
        records = timetable_ws.get_all_records(head=2)
        
        # Removes any empty keys/columns to keep data clean
        clean_records = [{k: v for k, v in record.items() if k != ''} for record in records]
        
        return jsonify({"status": "success", "data": clean_records}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    """Receives checkout data and appends it to the Logs tab."""
    try:
        data = request.json
        # Prepares the row to be added to the sheet
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
    """Deletes all entries in the Logs tab starting from Row 3 to protect headers."""
    try:
        # Fetch all values to see how many rows exist
        all_values = logs_ws.get_all_values()
        num_rows = len(all_values)

        # Since Row 1 is "A,B,C..." and Row 2 is "Timestamp, Activity...",
        # we only delete if there is data in Row 3 or below.
        if num_rows > 2:
            # delete_rows(start_index, end_index)
            # We start at 3 to keep your Row 2 headers safe
            logs_ws.delete_rows(3, num_rows)
            return jsonify({"status": "success", "message": f"Cleared {num_rows - 2} log entries."}), 200
        else:
            return jsonify({"status": "success", "message": "Logs are already empty (only headers exist)."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- RUN THE SERVER ---
if __name__ == '__main__':
    # Render assigns a dynamic port, so we use os.environ to find it
    port = int(os.environ.get('PORT', 5000))
    # host='0.0.0.0' is required for the cloud to access your Flask app
    app.run(host='0.0.0.0', port=port)