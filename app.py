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
    
    timetable_ws = sheet.worksheet("Timetable")
    logs_ws = sheet.worksheet("Logs")
    
    print("✅ Backend Live: Protecting Row 1 headers.")
except Exception as e:
    print(f"❌ Error: {e}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "DSA Timer Backend is live!"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        # head=2 is still correct if your Timetable sheet has a title in Row 1
        # If your Timetable headers are also in Row 1, change head=2 to head=1
        records = timetable_ws.get_all_records(head=2)
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

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    """Deletes everything below the first row."""
    try:
        all_values = logs_ws.get_all_values()
        num_rows = len(all_values)

        if num_rows > 1:
            # We start at Row 2 to keep your Row 1 headers safe
            logs_ws.delete_rows(2, num_rows)
            return jsonify({"status": "success", "message": f"Cleared {num_rows - 1} entries. Headers are safe."}), 200
        else:
            return jsonify({"status": "success", "message": "Logs are already empty."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)