# start of version v5.9.1 (Ripple Math + Real-Time Context)
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import gspread
from datetime import datetime, timedelta
import os
import google.generativeai as genai
import json
import pytz
import re 
import traceback
import logging
import threading

app = Flask(__name__)
CORS(app)

# --- 🌍 CONFIGURATION ---
IST = pytz.timezone('Asia/Kolkata')
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# --- 📊 SHEETS CONNECTION ---
timetable_ws = None
logs_ws = None
chat_logs_ws = None 

def init_sheets():
    global timetable_ws, logs_ws, chat_logs_ws
    try:
        creds_json = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        if creds_json:
            client = gspread.service_account_from_dict(json.loads(creds_json))
            sheet = client.open("overall_db")
            timetable_ws = sheet.worksheet("Timetable")
            logs_ws = sheet.worksheet("Logs")
            chat_logs_ws = sheet.worksheet("ChatLogs")
            app.logger.info("✅ Sheets Status: Gemini Systems Synchronized.")
            return True
        else:
            app.logger.error("❌ CRITICAL: GOOGLE_SHEETS_CREDS_JSON is missing.")
            return False
    except Exception as e:
        app.logger.error(f"❌ Sheets Error: {e}")
        return False

init_sheets()

# --- 🛠️ HELPERS ---
def sanitize_ts(ts_str):
    try:
        parts = ts_str.split(' ')
        h, m = parts[1].split(':')
        return f"{parts[0]} {h.zfill(2)}:{m.zfill(2)}"
    except: return ts_str

def parse_time_to_minutes(t_str):
    try:
        clean = re.sub(r'\s+', '', t_str.strip().upper())
        match = re.match(r"(\d+):(\d+)(AM|PM)?", clean)
        if not match: return 0
        h, m, mod = match.groups()
        h, m = int(h), int(m)
        if h == 12: h = 0
        if mod == "PM": h += 12
        return h * 60 + m
    except: return 0

def safe_float(val):
    try:
        if not val or str(val).strip() == '': return 0.0
        return float(val)
    except: return 0.0

# --- ☁️ CLOUD SYNC STATE ---
cloud_state = {
    "state": "READY",
    "activity": None,
    "start_time": None,
    "accumulated_seconds": 0 
}

# --- 🌐 ENDPOINTS ---
@app.route('/', methods=['GET'])
def health():
    return jsonify({
        "service": "Routine Flow Architect", 
        "version": "5.9.1", 
        "status": "Online",
        "model": "gemini-3-flash-preview"
    }), 200

@app.route('/get_state', methods=['GET'])
def get_state(): 
    return jsonify({"status": "success", "data": cloud_state}), 200

@app.route('/set_state', methods=['POST'])
def set_state():
    global cloud_state
    data = request.json
    cloud_state["state"] = data.get("state", "READY")
    cloud_state["activity"] = data.get("activity")
    cloud_state["start_time"] = data.get("start_time")
    cloud_state["accumulated_seconds"] = data.get("accumulated_seconds", 0) or 0
    return jsonify({"status": "success"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        if not timetable_ws: init_sheets()
        if not timetable_ws: return jsonify({"status": "error", "message": "DB ERROR"}), 500
        all_val = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_val[1] if h.strip()] 
        data = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        now = datetime.now(IST)
        curMin = (now.hour * 60) + now.minute
        cur_session = None
        for item in data:
            times = item.get('Time', '').split('-')
            if len(times) != 2: continue
            s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
            if (e < s and (curMin >= s or curMin < e)) or (s <= curMin < e):
                cur_session = item; break
        if not cur_session:
            return jsonify({"status": "success", "data": data, "cur": {"Activity": "BREAK", "Duration": "1"}, "prev": {"Activity": "---"}, "next": {"Activity": "---"}})
        idx = data.index(cur_session)
        return jsonify({
            "status": "success",
            "data": data,
            "prev": data[idx-1] if idx > 0 else data[-1],
            "cur": cur_session,
            "next": data[idx+1] if idx < len(data)-1 else data[0]
        })
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    try:
        if not logs_ws: init_sheets()
        if not logs_ws: return jsonify({"status": "error"}), 500
        raw_logs = logs_ws.get_all_values()
        if len(raw_logs) <= 1: return jsonify({"status": "success", "overall": None, "week": None}), 200
        headers = [h.strip() for h in raw_logs[0] if h.strip()]
        all_logs = [dict(zip(headers, r)) for r in raw_logs[1:] if any(r)]
        now = datetime.now(IST)
        start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)

        def process_subset(subset):
            if not subset: return {"study": 0, "adherence": 0, "debt": 0, "chart": [0.0]*7}
            keys = list(subset[0].keys()) if subset else []
            act_k = next((k for k in keys if 'actual' in k.lower()), None)
            debt_k = next((k for k in keys if 'debt' in k.lower()), None)
            ts_k = next((k for k in keys if 'time' in k.lower() or 'stamp' in k.lower()), None)
            valid_rows = [r for r in subset if str(r.get(act_k, '')).strip() or str(r.get(debt_k, '')).strip()]
            if not valid_rows: return {"study": 0, "adherence": 0, "debt": 0, "chart": [0.0]*7}
            total_study = sum(safe_float(r.get(act_k)) for r in valid_rows)
            total_debt = sum(safe_float(r.get(debt_k)) for r in valid_rows)
            completed = sum(1 for r in valid_rows if safe_float(r.get(act_k)) > 0)
            adherence = round((completed / len(valid_rows)) * 100)
            chart = [0.0] * 7
            for r in valid_rows:
                try:
                    dt = datetime.strptime(sanitize_ts(r.get(ts_k, '')), '%Y-%m-%d %H:%M')
                    chart[dt.weekday()] += safe_float(r.get(act_k))
                except: continue
            return {"study": round(total_study, 1), "adherence": adherence, "debt": round(total_debt, 1), "chart": chart}

        week_logs = []
        ts_key = next((k for k in all_logs[0].keys() if 'time' in k.lower() or 'stamp' in k.lower()), None)
        for r in all_logs:
            try:
                if IST.localize(datetime.strptime(sanitize_ts(r.get(ts_key, '')), '%Y-%m-%d %H:%M')) >= start_of_week:
                    week_logs.append(r)
            except: continue
        return jsonify({
            "status": "success", 
            "overall": process_subset(all_logs), 
            "week": process_subset(week_logs)
        }), 200
    except Exception as e: 
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500

def save_chat_bg(timestamp, user_message, ai_message):
    try:
        if chat_logs_ws:
            chat_logs_ws.append_rows([[timestamp, "User", user_message], [timestamp, "AI", ai_message]])
    except Exception as e:
        app.logger.error(f"Background chat save failed: {e}")

@app.route('/chat', methods=['POST'])
def chat():
    try:
        if not chat_logs_ws: init_sheets()
        user_msg = request.json.get('message')
        
        all_tt = timetable_ws.get_all_values()
        tt_headers = [h.strip() for h in all_tt[1] if h.strip()]
        timetable_data = [dict(zip(tt_headers, r)) for r in all_tt[2:] if any(r)]
        lean_tt = timetable_data[-10:]
        
        all_chat = chat_logs_ws.get_all_values()
        memory = []
        if len(all_chat) > 1:
            chat_headers = [h.strip() for h in all_chat[0] if h.strip()]
            memory = [dict(zip(chat_headers, r)) for r in all_chat[1:] if any(r)][-6:]
            
        # --- NEW: REAL-TIME CLOCK CONTEXT ---
        now = datetime.now(IST)
        cur_time_str = now.strftime('%I:%M %p')
        curMin = (now.hour * 60) + now.minute
        cur_activity = "Unknown"
        for item in timetable_data:
            times = item.get('Time', '').split('-')
            if len(times) == 2:
                s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
                if (e < s and (curMin >= s or curMin < e)) or (s <= curMin < e):
                    cur_activity = item.get('Activity', 'Unknown')
                    break
                    
        # V5.9.1 PROMPT UPGRADE
        prompt = f"""
        System: You are 'Routine Flow Architect', an AI assistant for Sriniket.
        Context: Sriniket is recovering from a bike accident.
        REAL-TIME STATUS: It is currently {cur_time_str}. The user's active current session is '{cur_activity}'.
        Schedule Context: {json.dumps(lean_tt)}
        Memory: {json.dumps(memory)}
        
        CRITICAL INSTRUCTIONS: 
        1. If the user asks a simple question, answer it DIRECTLY. Do NOT add unsolicited advice.
        2. ONLY provide schedule recommendations if explicitly asked.
        3. STRICT JSON RULE: You must output schedule changes as a JSON ARRAY of command objects. 
        
        Valid Actions:
        - "modify": Changes duration of an existing activity. (Requires "target", "new_val", "reason")
        - "delete": Removes an activity entirely. (Requires "target", "reason")
        - "insert": Adds a brand new activity at the current time. (Requires "activity", "duration", "reason")
        
        DURATION RULE: ALL durations MUST be a float followed by 'h' (e.g., "0.5h", "2.0h"). 
        
        User Input: {user_msg}
        
        Mandatory Change Format (Use ONLY if making schedule changes. Must be valid JSON array):
        ACTION_RECS: [{{"action": "delete", "target": "Study Session 4", "reason": "Emergency"}}, {{"action": "insert", "activity": "Doctor", "duration": "2.0h", "reason": "Checkup"}}]
        """
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # Streaming Generator Function
        def generate():
            full_text = ""
            try:
                response = model.generate_content(prompt, stream=True)
                for chunk in response:
                    if chunk.text:
                        full_text += chunk.text
                        yield f"data: {json.dumps({'text': chunk.text})}\n\n"
                
                ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
                threading.Thread(target=save_chat_bg, args=(ts, user_msg, full_text)).start()
                
                yield "data: [DONE]\n\n"
            except Exception as e:
                app.logger.error(f"Stream error: {str(e)}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return Response(stream_with_context(generate()), mimetype='text/event-stream')

    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

# --- PHASE 2: THE RIPPLE EFFECT ENGINE ---
@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    try:
        data = request.json
        # 'data' will now be a list of commands: [{"action": "delete", "target": "Study"}, {"action": "insert", "activity": "Doctor", "duration": "2.0h"}]
        
        all_val = timetable_ws.get_all_values()
        headers = [h.strip() for h in all_val[1] if h.strip()]
        current_schedule = [dict(zip(headers, r)) for r in all_val[2:] if any(r)]
        
        # 1. Execute AI Commands (Delete/Insert/Modify)
        for cmd in data:
            if cmd.get('action') == 'delete':
                current_schedule = [row for row in current_schedule if not re.match(rf'^{re.escape(cmd.get("target"))}$', row.get('Activity', ''), re.IGNORECASE)]
            
            elif cmd.get('action') == 'insert':
                now = datetime.now(IST)
                curMin = (now.hour * 60) + now.minute
                insert_idx = 0
                
                for idx, item in enumerate(current_schedule):
                    times = item.get('Time', '').split('-')
                    if len(times) == 2:
                        s = parse_time_to_minutes(times[0])
                        if s > curMin:
                            insert_idx = idx
                            break
                            
                new_block = {"Time": "TBD", "Activity": cmd.get("activity"), "Duration": cmd.get("duration").replace('h', '')}
                current_schedule.insert(insert_idx, new_block)
                
            elif cmd.get('action') == 'modify':
                 for row in current_schedule:
                     if re.match(rf'^{re.escape(cmd.get("target"))}$', row.get('Activity', ''), re.IGNORECASE):
                         row['Duration'] = cmd.get("new_val").replace('h', '')
                         break

        # 2. THE RIPPLE EFFECT: Recalculate all Start/End times
        if current_schedule:
            # Anchor to the first activity's start time to prevent the whole day from shifting
            first_time = current_schedule[0].get('Time', '').split('-')[0].strip()
            current_minutes = parse_time_to_minutes(first_time)
            
            for row in current_schedule:
                start_str = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"
                duration_mins = int(safe_float(row.get('Duration', 1.0)) * 60)
                current_minutes += duration_mins
                
                if current_minutes >= 1440:
                    current_minutes -= 1440
                    
                end_str = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"
                row['Time'] = f"{start_str}-{end_str}"

        # 3. Save back to Google Sheets
        timetable_ws.delete_rows(3, len(all_val)) 
        
        rows_to_insert = []
        for row in current_schedule:
             rows_to_insert.append([row.get('Time', ''), row.get('Activity', ''), row.get('Duration', '')])
             
        if rows_to_insert:
            timetable_ws.append_rows(rows_to_insert)

        return jsonify({"status": "success", "message": "Ripple effect applied."}), 200
        
    except Exception as e: 
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        d = request.json
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        logs_ws.append_row([ts, d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)])
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/bulk_log', methods=['POST'])
def bulk_log():
    try:
        if not logs_ws: init_sheets()
        data = request.json
        rows = [[datetime.now(IST).strftime('%Y-%m-%d %H:%M'), d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)] for d in data]
        logs_ws.append_rows(rows)
        return jsonify({"status": "success", "inserted": len(rows)}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/clear_chat', methods=['DELETE'])
def clear_chat():
    try:
        records = chat_logs_ws.get_all_values()
        if len(records) > 1: chat_logs_ws.delete_rows(2, len(records))
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/clear_logs', methods=['DELETE'])
def clear_logs():
    try:
        if not logs_ws: init_sheets()
        records = logs_ws.get_all_values()
        if len(records) > 1:
            logs_ws.delete_rows(2, len(records))
        return jsonify({"status": "success"}), 200
    except Exception as e: 
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
# end of version v5.9.1