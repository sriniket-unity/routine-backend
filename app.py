# start of version v8.0.0 (MongoDB Enterprise Core)
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import google.generativeai as genai
import json
import pytz
import re 
import traceback
import logging
import threading
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- 🌍 CONFIGURATION ---
IST = pytz.timezone('Asia/Kolkata')
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# --- 🗄️ MONGODB CONNECTION ---
MONGO_URI = os.environ.get("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.routine_flow

def seed_initial_data():
    """Seeds the DB with a basic template if it's completely empty."""
    default_schedule = []
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for day in days:
        default_schedule.extend([
            {"Day_Cell": day, "Time": "06:00 AM - 07:30 AM", "Activity": "Gym", "Duration": "1.5 hrs", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "07:30 AM - 08:30 AM", "Activity": "Breakfast & Freshen up", "Duration": "1 hr", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "08:30 AM - 11:30 AM", "Activity": "Study Session 1", "Duration": "3 hrs", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "11:30 AM - 12:00 PM", "Activity": "Break", "Duration": "0.5 hrs", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "12:00 PM - 02:00 PM", "Activity": "Study Session 2", "Duration": "2 hrs", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "02:00 PM - 03:00 PM", "Activity": "Lunch", "Duration": "1 hr", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "03:00 PM - 05:00 PM", "Activity": "Study Session 3", "Duration": "2 hrs", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "05:00 PM - 05:30 PM", "Activity": "Break", "Duration": "0.5 hrs", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "05:30 PM - 07:30 PM", "Activity": "Study Session 4", "Duration": "2 hrs", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "07:30 PM - 08:30 PM", "Activity": "Dinner", "Duration": "1 hr", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "08:30 PM - 09:30 PM", "Activity": "Study Session 5", "Duration": "1 hr", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "09:30 PM - 10:00 PM", "Activity": "Review / Planning", "Duration": "0.5 hr", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "10:00 PM - 10:30 PM", "Activity": "Wind down", "Duration": "0.5 hr", "Resolved_Day": day},
            {"Day_Cell": day, "Time": "10:30 PM - 06:00 AM", "Activity": "Sleep", "Duration": "7.5 hrs", "Resolved_Day": day}
        ])
    return default_schedule

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

# --- ☁️ CLOUD SYNC STATE (Now stored in DB for reliability) ---
def get_cloud_state():
    state = db.state.find_one({"_id": "timer_state"})
    if not state: return { "state": "READY", "activity": None, "start_time": None, "accumulated_seconds": 0 }
    state.pop('_id', None)
    return state

# --- 🌐 ENDPOINTS ---
@app.route('/', methods=['GET'])
def health():
    return jsonify({"service": "Routine Flow Architect", "version": "8.0.0", "status": "Online (MongoDB Core)"}), 200

@app.route('/get_state', methods=['GET'])
def get_state(): 
    return jsonify({"status": "success", "data": get_cloud_state()}), 200

@app.route('/set_state', methods=['POST'])
def set_state():
    data = request.json
    db.state.update_one({"_id": "timer_state"}, {"$set": {
        "state": data.get("state", "READY"), "activity": data.get("activity"), 
        "start_time": data.get("start_time"), "accumulated_seconds": data.get("accumulated_seconds", 0) or 0
    }}, upsert=True)
    return jsonify({"status": "success"}), 200

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        doc = db.schedules.find_one({"_id": "master_schedule"})
        if not doc or not doc.get("data"):
            data = seed_initial_data()
            db.schedules.update_one({"_id": "master_schedule"}, {"$set": {"data": data}}, upsert=True)
        else:
            data = doc.get("data")
            
        formatted_data = []
        for item in data:
            formatted_data.append({ "Day": item.get('Resolved_Day', item.get('Day_Cell', 'Monday')), "Time": item.get('Time'), "Activity": item.get('Activity'), "Duration": item.get('Duration') })

        now = datetime.now(IST)
        cur_day_name = now.strftime('%A')
        curMin = (now.hour * 60) + now.minute
        if now.hour < 8: cur_day_name = (now - timedelta(days=1)).strftime('%A')

        today_data = [item for item in formatted_data if item['Day'] == cur_day_name]
        if not today_data: today_data = formatted_data 

        cur_session = None
        for item in today_data:
            times = item.get('Time', '').split('-')
            if len(times) != 2: continue
            s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
            if (e < s and (curMin >= s or curMin < e)) or (s <= curMin < e):
                cur_session = item; break
                
        if not cur_session:
            return jsonify({"status": "success", "data": today_data, "full_data": formatted_data, "cur": {"Activity": "BREAK", "Duration": "1"}, "prev": {"Activity": "---"}, "next": {"Activity": "---"}})
        
        idx = today_data.index(cur_session)
        return jsonify({
            "status": "success", "data": today_data, "full_data": formatted_data, 
            "prev": today_data[idx-1] if idx > 0 else today_data[-1], "cur": cur_session, "next": today_data[idx+1] if idx < len(today_data)-1 else today_data[0]
        })
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

def save_chat_bg(timestamp, user_message, ai_message):
    try:
        db.chat.insert_many([
            {"timestamp": timestamp, "role": "User", "text": user_message},
            {"timestamp": timestamp, "role": "AI", "text": ai_message}
        ])
    except: pass

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_msg = request.json.get('message')
        doc = db.schedules.find_one({"_id": "master_schedule"})
        timetable_data = doc.get("data", []) if doc else []
            
        now = datetime.now(IST)
        cur_day_name = now.strftime('%A')
        curMin = (now.hour * 60) + now.minute
        if now.hour < 8: cur_day_name = (now - timedelta(days=1)).strftime('%A')

        cur_idx, cur_activity = 0, "Unknown"
        formatted_data = []
        for i, item in enumerate(timetable_data):
            day = item.get('Resolved_Day', item.get('Day_Cell', 'Monday'))
            formatted_data.append({"Day": day, "Time": item.get('Time'), "Activity": item.get('Activity'), "Duration": item.get('Duration')})
            if day == cur_day_name:
                times = item.get('Time', '').split('-')
                if len(times) == 2:
                    s, e = parse_time_to_minutes(times[0]), parse_time_to_minutes(times[1])
                    if (e < s and (curMin >= s or curMin < e)) or (s <= curMin < e):
                        cur_activity = item.get('Activity', 'Unknown')
                        cur_idx = i; break
        
        lean_tt = []
        if len(formatted_data) > 0:
            for i in range(15): lean_tt.append(formatted_data[(cur_idx + i) % len(formatted_data)])

        recent_chat = list(db.chat.find({}, {"_id": 0}).sort("_id", -1).limit(6))
        recent_chat.reverse()
        memory = [{"Timestamp": m["timestamp"], "Role": m["role"], "Message": m["text"]} for m in recent_chat]
            
        p_doc = db.settings.find_one({"_id": "priorities"})
        user_priorities = p_doc.get("data", {}) if p_doc else {}
                    
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
        
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
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
            except Exception as e: yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return Response(stream_with_context(generate()), mimetype='text/event-stream')
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_timetable', methods=['PATCH'])
def update_timetable():
    try:
        data = request.json
        doc = db.schedules.find_one({"_id": "master_schedule"})
        current_schedule = doc.get("data", []) if doc else []
        
        # Save Snapshot before modification
        db.schedules.update_one({"_id": "snapshot_schedule"}, {"$set": {"data": current_schedule}}, upsert=True)

        now = datetime.now(IST)
        cur_day_name = now.strftime('%A')
        if now.hour < 8: cur_day_name = (now - timedelta(days=1)).strftime('%A')
        curMin = (now.hour * 60) + now.minute

        for cmd in data:
            action = cmd.get('action')
            target = cmd.get('target', '')

            if action == 'delete':
                for idx, row in enumerate(current_schedule):
                    if row.get('Resolved_Day') == cur_day_name and re.match(rf'^{re.escape(target)}$', row.get('Activity', ''), re.IGNORECASE):
                        current_schedule.pop(idx); break 
            elif action == 'modify':
                 for idx, row in enumerate(current_schedule):
                     if row.get('Resolved_Day') == cur_day_name and re.match(rf'^{re.escape(target)}$', row.get('Activity', ''), re.IGNORECASE):
                         row['Duration'] = cmd.get("new_val").replace('h', ''); break
            elif action == 'insert':
                insert_idx = len(current_schedule)
                for idx, row in enumerate(current_schedule):
                    if row.get('Resolved_Day') == cur_day_name:
                        times = row.get('Time', '').split('-')
                        if len(times) == 2:
                            if parse_time_to_minutes(times[0]) > curMin:
                                insert_idx = idx; break
                new_block = {"Day_Cell": cur_day_name, "Time": "TBD", "Activity": cmd.get("activity"), "Duration": cmd.get("duration").replace('h', ''), "Resolved_Day": cur_day_name}
                current_schedule.insert(insert_idx, new_block)

        def format_12hr(mins):
            h, m = (mins // 60) % 24, mins % 60
            ampm, h12 = "AM" if h < 12 else "PM", h % 12
            if h12 == 0: h12 = 12
            return f"{h12:02d}:{m:02d} {ampm}"

        if current_schedule:
            day_anchors = {}
            for row in current_schedule:
                day = row.get('Resolved_Day')
                if day not in day_anchors:
                    times = row.get('Time', '').split('-')
                    day_anchors[day] = parse_time_to_minutes(times[0]) if len(times) > 0 else 360 

            for day, anchor_mins in day_anchors.items():
                current_minutes = anchor_mins
                for row in current_schedule:
                    if row.get('Resolved_Day') == day:
                        start_str = format_12hr(current_minutes)
                        duration_val = safe_float(row.get('Duration', 1.0))
                        current_minutes += int(duration_val * 60)
                        if current_minutes >= 1440: current_minutes -= 1440
                        row['Time'] = f"{start_str} - {format_12hr(current_minutes)}"
                        row['Duration'] = f"{int(duration_val) if duration_val.is_integer() else duration_val} {'hr' if duration_val == 1.0 else 'hrs'}"

        db.schedules.update_one({"_id": "master_schedule"}, {"$set": {"data": current_schedule}}, upsert=True)
        return jsonify({"status": "success", "message": "Ripple effect applied."}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/revert_timetable', methods=['POST'])
def revert_timetable():
    try:
        snap_doc = db.schedules.find_one({"_id": "snapshot_schedule"})
        if not snap_doc or not snap_doc.get("data"): return jsonify({"status": "error", "message": "No snapshot data found."}), 400
        
        db.schedules.update_one({"_id": "master_schedule"}, {"$set": {"data": snap_doc.get("data")}}, upsert=True)
        db.schedules.update_one({"_id": "snapshot_schedule"}, {"$set": {"data": []}}, upsert=True)
        return jsonify({"status": "success", "message": "Reverted successfully."}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_priorities', methods=['GET'])
def get_priorities():
    try:
        doc = db.schedules.find_one({"_id": "master_schedule"})
        tt_data = doc.get("data", []) if doc else []
        
        unique_activities = set()
        for r in tt_data:
            act_name = r.get('Activity', '').strip()
            if act_name:
                if "study" in act_name.lower(): act_name = "Study"
                unique_activities.add(act_name)
                    
        p_doc = db.settings.find_one({"_id": "priorities"})
        saved_priorities = p_doc.get("data", {}) if p_doc else {}
                    
        final_priorities = {}
        for act in unique_activities: final_priorities[act] = saved_priorities.get(act, 5) 
        return jsonify({"status": "success", "data": final_priorities}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/save_priorities', methods=['POST'])
def save_priorities():
    try:
        db.settings.update_one({"_id": "priorities"}, {"$set": {"data": request.json}}, upsert=True)
        return jsonify({"status": "success", "message": "Priorities synced."}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/log_session', methods=['POST'])
def log_session():
    try:
        d = request.json
        d['timestamp'] = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        db.logs.insert_one(d)
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/clear_chat', methods=['DELETE'])
def clear_chat():
    try:
        db.chat.delete_many({})
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    try:
        all_logs = list(db.logs.find({}, {"_id": 0}))
        if not all_logs: return jsonify({"status": "success", "overall": None, "week": None}), 200
        
        now = datetime.now(IST)
        start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)

        def process_subset(subset):
            if not subset: return {"study": 0, "adherence": 0, "total_debt": 0, "debts_by_activity": {}, "chart": [0.0]*7}
            valid_rows = [r for r in subset if str(r.get('actual_duration', '')).strip() or str(r.get('time_debt', '')).strip()]
            if not valid_rows: return {"study": 0, "adherence": 0, "total_debt": 0, "debts_by_activity": {}, "chart": [0.0]*7}
            
            total_study, total_debt, debts_by_activity = 0, 0, {}
            for r in valid_rows:
                act_name = str(r.get('activity', 'Unknown')).strip()
                if "study" in act_name.lower(): act_name = "Study"
                
                actual_val = safe_float(r.get('actual_duration'))
                debt_val = safe_float(r.get('time_debt'))
                
                if act_name == "Study": total_study += actual_val
                total_debt += debt_val
                if debt_val > 0: debts_by_activity[act_name] = round(debts_by_activity.get(act_name, 0) + debt_val, 1)
                    
            adherence = round((sum(1 for r in valid_rows if safe_float(r.get('actual_duration')) > 0) / len(valid_rows)) * 100)
            
            chart = [0.0] * 7
            for r in valid_rows:
                try:
                    dt = datetime.strptime(sanitize_ts(r.get('timestamp', '')), '%Y-%m-%d %H:%M')
                    if "study" in str(r.get('activity', '')).lower(): chart[dt.weekday()] += safe_float(r.get('actual_duration'))
                except: continue
            return { "study": round(total_study, 1), "adherence": adherence, "total_debt": round(total_debt, 1), "debts_by_activity": debts_by_activity, "chart": chart }

        week_logs = [r for r in all_logs if IST.localize(datetime.strptime(sanitize_ts(r.get('timestamp', '')), '%Y-%m-%d %H:%M')) >= start_of_week]
        return jsonify({ "status": "success", "overall": process_subset(all_logs), "week": process_subset(week_logs) }), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
# end of version v8.0.0