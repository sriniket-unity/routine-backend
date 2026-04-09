# start of version v7.3.2 (Scoped Deletes & Day-Anchored Ripple)
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
snapshot_ws = None 
priority_ws = None 

def init_sheets():
    global timetable_ws, logs_ws, chat_logs_ws, snapshot_ws, priority_ws
    try:
        creds_json = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
        if creds_json:
            client = gspread.service_account_from_dict(json.loads(creds_json))
            sheet = client.open("overall_db")
            timetable_ws = sheet.worksheet("Timetable")
            logs_ws = sheet.worksheet("Logs")
            chat_logs_ws = sheet.worksheet("ChatLogs")
            try: snapshot_ws = sheet.worksheet("Snapshot")
            except: snapshot_ws = None
            try: priority_ws = sheet.worksheet("Priority")
            except: priority_ws = None
            app.logger.info("✅ Sheets Status: Gemini Systems Synchronized.")
            return True
        else:
            return False
    except Exception as e:
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
        clean_val = re.sub(r'[^\d.]', '', str(val))
        return float(clean_val) if clean_val else 0.0
    except: return 0.0

# --- ☁️ CLOUD SYNC STATE ---
cloud_state = { "state": "READY", "activity": None, "start_time": None, "accumulated_seconds": 0 }

# --- 🌐 ENDPOINTS ---
@app.route('/', methods=['GET'])
def health():
    return jsonify({"service": "Routine Flow Architect", "version": "7.3.2", "status": "Online"}), 200

@app.route('/get_state', methods=['GET'])
def get_state(): 
    return jsonify({"status": "success", "data": cloud_state}), 200

@app.route('/set_state', methods=['POST'])
def set_state():
    global cloud_state
    data = request.json
    cloud_state.update({"state": data.get("state", "READY"), "activity": data.get("activity"), "start_time": data.get("start_time"), "accumulated_seconds": data.get("accumulated_seconds", 0) or 0})
    return jsonify({"status": "success"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        if not timetable_ws: init_sheets()
        if not timetable_ws: return jsonify({"status": "error", "message": "DB ERROR"}), 500
        
        all_val = timetable_ws.get_all_values()
        data = []
        current_day = "Monday"
        
        for r in all_val[2:]:
            if not r or str(r[0]).strip().lower() == 'metric' or (len(r) > 2 and 'hours' in str(r[2]).lower()): break
            if str(r[0]).strip(): current_day = str(r[0]).strip()
            while len(r) < 4: r.append('')
            
            time_str, act_str, dur_str = str(r[1]).strip(), str(r[2]).strip(), str(r[3]).strip()
            if time_str and act_str:
                data.append({ "Day": current_day, "Time": time_str, "Activity": act_str, "Duration": dur_str })

        now = datetime.now(IST)
        cur_day_name = now.strftime('%A')
        curMin = (now.hour * 60) + now.minute
        if now.hour < 8: cur_day_name = (now - timedelta(days=1)).strftime('%A')

        today_data = [item for item in data if item['Day'] == cur_day_name]
        if not today_data: today_data = data 

        cur_session = None
        for item in today_data:
            times = item.get('Time', '').split('-')
            if len(times) != 2: continue
            s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
            if (e < s and (curMin >= s or curMin < e)) or (s <= curMin < e):
                cur_session = item; break
                
        if not cur_session:
            return jsonify({"status": "success", "data": today_data, "full_data": data, "cur": {"Activity": "BREAK", "Duration": "1"}, "prev": {"Activity": "---"}, "next": {"Activity": "---"}})
        
        idx = today_data.index(cur_session)
        return jsonify({
            "status": "success", 
            "data": today_data,
            "full_data": data, 
            "prev": today_data[idx-1] if idx > 0 else today_data[-1],
            "cur": cur_session,
            "next": today_data[idx+1] if idx < len(today_data)-1 else today_data[0]
        })
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

def save_chat_bg(timestamp, user_message, ai_message):
    try:
        if chat_logs_ws: chat_logs_ws.append_rows([[timestamp, "User", user_message], [timestamp, "AI", ai_message]])
    except: pass

@app.route('/chat', methods=['POST'])
def chat():
    try:
        if not chat_logs_ws: init_sheets()
        user_msg = request.json.get('message')
        
        all_tt = timetable_ws.get_all_values()
        timetable_data = []
        current_day = "Monday"
        for r in all_tt[2:]:
            if not r or str(r[0]).strip().lower() == 'metric': break
            if str(r[0]).strip(): current_day = str(r[0]).strip()
            while len(r) < 4: r.append('')
            if str(r[1]).strip() and str(r[2]).strip():
                timetable_data.append({ "Day": current_day, "Time": str(r[1]).strip(), "Activity": str(r[2]).strip(), "Duration": str(r[3]).strip() })
            
        now = datetime.now(IST)
        cur_day_name = now.strftime('%A')
        curMin = (now.hour * 60) + now.minute
        if now.hour < 8: cur_day_name = (now - timedelta(days=1)).strftime('%A')

        cur_idx = 0
        cur_activity = "Unknown"
        for i, item in enumerate(timetable_data):
            if item['Day'] == cur_day_name:
                times = item.get('Time', '').split('-')
                if len(times) == 2:
                    s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
                    if (e < s and (curMin >= s or curMin < e)) or (s <= curMin < e):
                        cur_activity = item.get('Activity', 'Unknown')
                        cur_idx = i
                        break
        
        lean_tt = []
        if len(timetable_data) > 0:
            for i in range(15):
                lean_tt.append(timetable_data[(cur_idx + i) % len(timetable_data)])

        all_chat = chat_logs_ws.get_all_values()
        memory = [dict(zip([h.strip() for h in all_chat[0]], r)) for r in all_chat[1:] if any(r)][-6:] if len(all_chat) > 1 else []
            
        user_priorities = {}
        if priority_ws:
            p_data = priority_ws.get_all_values()
            for r in p_data:
                if len(r) >= 2 and r[0].strip(): user_priorities[r[0].strip()] = int(safe_float(r[1]))
                    
        prompt = f"""
        System: You are 'Routine Flow Architect', an elite AI assistant for Sriniket.
        Context: Sriniket is recovering from a bike accident.
        REAL-TIME STATUS: Today is officially {cur_day_name}. It is currently {now.strftime('%I:%M %p')}. Active session: '{cur_activity}'.
        
        USER PRIORITY MATRIX (0-10 Scale):
        {json.dumps(user_priorities)}
        
        Upcoming 7-Day Schedule Context (Next 15 Blocks): {json.dumps(lean_tt)}
        Memory: {json.dumps(memory)}
        
        CRITICAL INSTRUCTIONS - SACRIFICE MATH: 
        If the user asks to insert a new activity, resolve the time conflict using their Priority Matrix:
        1. Expendable (Score 0-3): Target these FIRST for deletion.
        2. Flexible (Score 4-7): Shrink their duration to absorb impact.
        3. Vital (Score 8-10): NEVER delete or shrink these activities. Preserve at all costs.
        
        Valid Actions for ACTION_RECS JSON Array:
        - "modify": Changes duration of an existing activity. (Requires "target", "new_val", "reason")
        - "delete": Removes an activity entirely. (Requires "target", "reason")
        - "insert": Adds a brand new activity at the current time. (Requires "activity", "duration", "reason")
        
        DURATION RULE: ALL durations MUST be a float followed by 'h' (e.g., "0.5h"). 
        
        User Input: {user_msg}
        
        Mandatory Format (Use ONLY if making schedule changes. Must be valid JSON array):
        ACTION_RECS: [{{"action": "delete", "target": "Wind down", "reason": "Sacrificed low priority task for emergency"}}]
        """
        
        model = genai.GenerativeModel('gemini-1.5-flash')
        
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
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return Response(stream_with_context(generate()), mimetype='text/event-stream')
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    try:
        data = request.json
        if not snapshot_ws: init_sheets()
        
        all_a_to_d = timetable_ws.get('A3:D150') 
        current_schedule = []
        
        # 1. Map rows to exact days (Resolving Merged Cells)
        temp_day = "Monday"
        for r in all_a_to_d:
            if not r or (len(r) > 0 and r[0].strip().lower() == 'metric') or (len(r) > 2 and 'hours' in str(r[2]).lower()): 
                break
            while len(r) < 4: r.append('')
            if str(r[0]).strip(): temp_day = str(r[0]).strip()
            current_schedule.append({"Day_Cell": temp_day, "Time": r[1], "Activity": r[2], "Duration": r[3], "Resolved_Day": temp_day})
            
        original_length = len(current_schedule)
        
        if snapshot_ws:
             snapshot_ws.clear()
             snapshot_data = [[row.get('Day_Cell', ''), row.get('Time', ''), row.get('Activity', ''), row.get('Duration', '')] for row in current_schedule]
             if snapshot_data: snapshot_ws.append_rows(snapshot_data)

        now = datetime.now(IST)
        cur_day_name = now.strftime('%A')
        if now.hour < 8: cur_day_name = (now - timedelta(days=1)).strftime('%A')
        curMin = (now.hour * 60) + now.minute

        # 2. Scoped Commands (Only affect current day)
        for cmd in data:
            action = cmd.get('action')
            target = cmd.get('target', '')

            if action == 'delete':
                for idx, row in enumerate(current_schedule):
                    if row['Resolved_Day'] == cur_day_name and re.match(rf'^{re.escape(target)}$', row.get('Activity', ''), re.IGNORECASE):
                        current_schedule.pop(idx)
                        break 

            elif action == 'modify':
                 for idx, row in enumerate(current_schedule):
                     if row['Resolved_Day'] == cur_day_name and re.match(rf'^{re.escape(target)}$', row.get('Activity', ''), re.IGNORECASE):
                         row['Duration'] = cmd.get("new_val").replace('h', '')
                         break

            elif action == 'insert':
                insert_idx = len(current_schedule)
                for idx, row in enumerate(current_schedule):
                    if row['Resolved_Day'] == cur_day_name:
                        times = row.get('Time', '').split('-')
                        if len(times) == 2:
                            s = parse_time_to_minutes(times[0])
                            if s > curMin:
                                insert_idx = idx
                                break
                new_block = {"Day_Cell": cur_day_name, "Time": "TBD", "Activity": cmd.get("activity"), "Duration": cmd.get("duration").replace('h', ''), "Resolved_Day": cur_day_name}
                current_schedule.insert(insert_idx, new_block)

        def format_12hr(mins):
            h = (mins // 60) % 24
            m = mins % 60
            ampm = "AM" if h < 12 else "PM"
            h12 = h % 12
            if h12 == 0: h12 = 12
            return f"{h12:02d}:{m:02d} {ampm}"

        # 3. Day-Anchored Ripple (Prevents pushing the whole week out of sync)
        if current_schedule:
            day_anchors = {}
            for row in current_schedule:
                day = row['Resolved_Day']
                if day not in day_anchors:
                    times = row.get('Time', '').split('-')
                    if len(times) > 0: day_anchors[day] = parse_time_to_minutes(times[0])
                    else: day_anchors[day] = 360 # Default 6 AM

            for day, anchor_mins in day_anchors.items():
                current_minutes = anchor_mins
                for row in current_schedule:
                    if row['Resolved_Day'] == day:
                        start_str = format_12hr(current_minutes)
                        duration_val = safe_float(row.get('Duration', 1.0))
                        duration_mins = int(duration_val * 60)
                        current_minutes += duration_mins
                        if current_minutes >= 1440: current_minutes -= 1440
                        end_str = format_12hr(current_minutes)
                        row['Time'] = f"{start_str} - {end_str}"
                        row['Duration'] = f"{int(duration_val) if duration_val.is_integer() else duration_val} {'hr' if duration_val == 1.0 else 'hrs'}"

        timetable_ws.batch_clear([f'A3:D{3 + original_length}'])
        # Writes the flat DB back to sheets, unmerging visually to prevent glitches
        rows_to_update = [[row.get('Resolved_Day', ''), row.get('Time', ''), row.get('Activity', ''), row.get('Duration', '')] for row in current_schedule]
        if rows_to_update:
            timetable_ws.update(f'A3:D{2 + len(rows_to_update)}', rows_to_update)

        return jsonify({"status": "success", "message": "Ripple effect applied."}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/revert_timetable', methods=['POST'])
def revert_timetable():
    try:
        if not snapshot_ws: init_sheets()
        if not snapshot_ws: return jsonify({"status": "error", "message": "Snapshot worksheet missing."}), 500

        snapshot_data = snapshot_ws.get_all_values()
        if not snapshot_data: return jsonify({"status": "error", "message": "No snapshot data found."}), 400
            
        original_length = len(timetable_ws.get('A3:D150'))
        timetable_ws.batch_clear([f'A3:D{3 + original_length}'])
        if snapshot_data: timetable_ws.update(f'A3:D{2 + len(snapshot_data)}', snapshot_data)
        snapshot_ws.clear()
        
        return jsonify({"status": "success", "message": "Reverted successfully."}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_priorities', methods=['GET'])
def get_priorities():
    try:
        if not priority_ws or not timetable_ws: init_sheets()
        
        all_tt = timetable_ws.get_all_values()
        unique_activities = set()
        if len(all_tt) > 2:
            headers = [h.strip() for h in all_tt[1]]
            act_idx = headers.index('Activity') if 'Activity' in headers else 2
            for r in all_tt[2:]:
                if not r or r[0].strip().lower() == 'metric' or 'hours' in str(r).lower(): break
                if len(r) > act_idx and str(r[act_idx]).strip():
                    act_name = str(r[act_idx]).strip()
                    if "study" in act_name.lower(): act_name = "Study"
                    unique_activities.add(act_name)
                    
        saved_priorities = {}
        if priority_ws:
            p_data = priority_ws.get_all_values()
            for r in p_data:
                if len(r) >= 2 and r[0].strip(): saved_priorities[r[0].strip()] = int(safe_float(r[1]))
                    
        final_priorities = {}
        for act in unique_activities: final_priorities[act] = saved_priorities.get(act, 5) 
        return jsonify({"status": "success", "data": final_priorities}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/save_priorities', methods=['POST'])
def save_priorities():
    try:
        data = request.json
        if not priority_ws: init_sheets()
        if priority_ws:
            priority_ws.clear()
            rows = [[k, v] for k, v in data.items()]
            if rows: priority_ws.append_rows(rows)
        return jsonify({"status": "success", "message": "Priorities synced to cloud."}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        d = request.json
        ts = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        logs_ws.append_row([ts, d.get('activity'), d.get('planned_duration'), d.get('actual_duration'), d.get('time_debt', 0)])
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/clear_chat', methods=['DELETE'])
def clear_chat():
    try:
        if not chat_logs_ws: init_sheets()
        records = chat_logs_ws.get_all_values()
        if len(records) > 1: chat_logs_ws.delete_rows(2, len(records))
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

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
            if not subset: return {"study": 0, "adherence": 0, "total_debt": 0, "debts_by_activity": {}, "chart": [0.0]*7}
            keys = list(subset[0].keys()) if subset else []
            act_k = next((k for k in keys if 'actual' in k.lower()), None)
            debt_k = next((k for k in keys if 'debt' in k.lower()), None)
            ts_k = next((k for k in keys if 'time' in k.lower() or 'stamp' in k.lower()), None)
            name_k = next((k for k in keys if 'activity' in k.lower() or 'name' in k.lower()), keys[1] if len(keys)>1 else 'Activity')
            
            valid_rows = [r for r in subset if str(r.get(act_k, '')).strip() or str(r.get(debt_k, '')).strip()]
            if not valid_rows: return {"study": 0, "adherence": 0, "total_debt": 0, "debts_by_activity": {}, "chart": [0.0]*7}
            
            total_study = 0
            total_debt = 0
            debts_by_activity = {}
            
            for r in valid_rows:
                act_name = str(r.get(name_k, 'Unknown')).strip()
                if "study" in act_name.lower(): act_name = "Study"
                
                actual_val = safe_float(r.get(act_k))
                debt_val = safe_float(r.get(debt_k))
                
                if act_name == "Study": total_study += actual_val
                total_debt += debt_val
                
                if debt_val > 0: debts_by_activity[act_name] = round(debts_by_activity.get(act_name, 0) + debt_val, 1)
                    
            completed = sum(1 for r in valid_rows if safe_float(r.get(act_k)) > 0)
            adherence = round((completed / len(valid_rows)) * 100)
            
            chart = [0.0] * 7
            for r in valid_rows:
                try:
                    dt = datetime.strptime(sanitize_ts(r.get(ts_k, '')), '%Y-%m-%d %H:%M')
                    if "study" in str(r.get(name_k, '')).lower(): chart[dt.weekday()] += safe_float(r.get(act_k))
                except: continue
                
            return { "study": round(total_study, 1), "adherence": adherence, "total_debt": round(total_debt, 1), "debts_by_activity": debts_by_activity, "chart": chart }

        week_logs = []
        ts_key = next((k for k in all_logs[0].keys() if 'time' in k.lower() or 'stamp' in k.lower()), None)
        for r in all_logs:
            try:
                if IST.localize(datetime.strptime(sanitize_ts(r.get(ts_key, '')), '%Y-%m-%d %H:%M')) >= start_of_week: week_logs.append(r)
            except: continue
            
        return jsonify({ "status": "success", "overall": process_subset(all_logs), "week": process_subset(week_logs) }), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
# end of version v7.3.2