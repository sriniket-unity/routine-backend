from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from datetime import datetime

app = Flask(__name__)
CORS(app)

# --- GOOGLE SHEETS SETUP ---
try:
    # Connects using your credentials.json
    client = gspread.service_account(filename='credentials.json')
    
    # Opens your sheet named "overall_db"
    sheet = client.open("overall_db")
    
    # Connects to your specific tabs
    timetable_ws = sheet.worksheet("Timetable")
    logs_ws = sheet.worksheet("Logs")
    
    print("✅ Successfully connected to Google Sheets!")
except Exception as e:
    print(f"❌ Error connecting to sheet: {e}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "DSA Timer Backend is running!"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    """Reads the master timetable and sends it to your app."""
    try:
        # head=2 tells Python your actual headers (Day, Time, Activity) are on Row 2
        records = timetable_ws.get_all_records(head=2)
        
        # This part removes any empty columns/keys to prevent "duplicate header" errors
        clean_records = [{k: v for k, v in record.items() if k != ''} for record in records]
        
        return jsonify({"status": "success", "data": clean_records}), 200
    except Exception as e:
        # Providing a more descriptive error message if it still fails
        return jsonify({
            "status": "error", 
            "message": f"Check Row 2 of your 'Timetable' tab for duplicate or empty headers. Error: {str(e)}"
        }), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    """Receives checkout data from your app and appends it to the Logs tab."""
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
        return jsonify({"status": "success", "message": "Logged successfully to overall_db!"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # Runs locally on port 5000
    app.run(debug=True, port=5000)