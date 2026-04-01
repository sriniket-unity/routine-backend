from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime
import os

app = Flask(__name__)
CORS(app)

# --- GOOGLE SHEETS SETUP ---
try:
    client = gspread.service_account(filename='credentials.json')
    sheet = client.open("overall_db")
    
    # Selecting your tabs
    timetable_ws = sheet.worksheet("Timetable")
    logs_ws = sheet.worksheet("Logs")
    
    print("✅ Backend Live: Protecting Row 1 headers only.")
except Exception as e:
    print(f"❌ Error: {e}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "DSA Timer Backend is live!"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    """Reads the master timetable."""
    try:
        # Note: head=1 assumes your Timetable headers are also in Row 1.
        # If your Timetable has a title in Row 1, keep this as head=2.
        records = timetable_ws.get_all_records(head=1)
        clean_records = [{k: v for k, v in record.items() if k != ''} for record in records]
        return jsonify({"status": "success", "data": clean_records}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    """Appends a new study session to the Logs tab."""
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

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    """Wipes everything except the header in Row 1."""
    try:
        all_values = logs_ws.get_all_values()
        num_rows = len(all_values)

        # If more than 1 row exists, we have data to delete
        if num_rows > 1:
            # We start at Row 2 to keep your Row 1 headers safe
            logs_ws.delete_rows(2, num_rows)
            return jsonify({"status": "success", "message": f"Cleared {num_rows - 1} entries. Row 1 header is safe."}), 200
        else:
            return jsonify({"status": "success", "message": "Logs are already empty."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)