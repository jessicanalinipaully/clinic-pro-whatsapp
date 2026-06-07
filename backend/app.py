from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import os, json, requests
from datetime import datetime, date, timedelta

app = Flask(__name__)
CORS(app)

EXCEL_FILE = "database_v3.xlsx"
VERIFY_TOKEN = "clinic_verify_123"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")


def to_am_pm(t):
    return datetime.strptime(str(t), "%H:%M").strftime("%I:%M %p")


def today_iso():
    return date.today().strftime("%Y-%m-%d")


def is_valid_date(d):
    try:
        return datetime.strptime(str(d), "%Y-%m-%d").date() >= date.today()
    except:
        return False


def create_excel_if_missing():
    if not os.path.exists(EXCEL_FILE):
        patients = pd.DataFrame(columns=["patient_id", "name", "phone"])

        doctors = pd.DataFrame([
            {"doctor_id": "1", "name": "Dr Priya", "specialization": "Dermatologist", "working_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday", "start_time": "09:00", "end_time": "18:00", "slot_minutes": "30"},
            {"doctor_id": "2", "name": "Dr Kumar", "specialization": "Dentist", "working_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday", "start_time": "09:00", "end_time": "18:00", "slot_minutes": "30"},
            {"doctor_id": "3", "name": "Dr Mehta", "specialization": "General Physician", "working_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday", "start_time": "09:00", "end_time": "18:00", "slot_minutes": "30"},
        ], dtype=object)

        appointments = pd.DataFrame(columns=[
            "appointment_id", "patient_name", "phone", "doctor_id", "doctor_name",
            "date", "time", "status", "created_at", "reminder_sent"
        ])

        conversations = pd.DataFrame(columns=[
            "phone", "patient_name", "step", "doctor_id", "date",
            "time_period", "appointment_id"
        ])

        write_all_sheets(patients, doctors, appointments, conversations)


def read_sheet(name):
    create_excel_if_missing()
    try:
        return pd.read_excel(EXCEL_FILE, sheet_name=name, dtype=object).fillna("").astype(object)
    except:
        return pd.DataFrame()


def ensure_columns(df, cols):
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df.astype(object)


def load_all():
    patients = ensure_columns(read_sheet("patients"), ["patient_id", "name", "phone"])

    doctors = ensure_columns(read_sheet("doctors"), [
        "doctor_id", "name", "specialization", "working_days",
        "start_time", "end_time", "slot_minutes"
    ])

    appointments = ensure_columns(read_sheet("appointments"), [
        "appointment_id", "patient_name", "phone", "doctor_id",
        "doctor_name", "date", "time", "status", "created_at", "reminder_sent"
    ])

    conversations = ensure_columns(read_sheet("conversations"), [
        "phone", "patient_name", "step", "doctor_id", "date",
        "time_period", "appointment_id"
    ])

    for i in doctors.index:
        doctors.loc[i, "working_days"] = doctors.loc[i, "working_days"] or "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
        doctors.loc[i, "start_time"] = doctors.loc[i, "start_time"] or "09:00"
        doctors.loc[i, "end_time"] = doctors.loc[i, "end_time"] or "18:00"
        doctors.loc[i, "slot_minutes"] = doctors.loc[i, "slot_minutes"] or "30"

    return patients, doctors, appointments, conversations


def write_all_sheets(patients, doctors, appointments, conversations):
    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
        patients.astype(object).to_excel(writer, "patients", index=False)
        doctors.astype(object).to_excel(writer, "doctors", index=False)
        appointments.astype(object).to_excel(writer, "appointments", index=False)
        conversations.astype(object).to_excel(writer, "conversations", index=False)


def next_id(df, col):
    if df.empty:
        return "1"
    nums = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return str(int(nums.max()) + 1)


def get_doctor_slots(doctor_id, selected_date, doctors):
    doctor = doctors[doctors["doctor_id"].astype(str) == str(doctor_id)]
    if doctor.empty:
        return []

    doctor = doctor.iloc[0]
    day = datetime.strptime(selected_date, "%Y-%m-%d").strftime("%A")
    working_days = [d.strip() for d in str(doctor["working_days"]).split(",")]

    if day not in working_days:
        return []

    start = datetime.strptime(str(doctor["start_time"]), "%H:%M")
    end = datetime.strptime(str(doctor["end_time"]), "%H:%M")
    minutes = int(str(doctor["slot_minutes"] or "30"))

    slots = []
    current = start

    while current < end:
        t = current.strftime("%H:%M")
        if t < "13:00" or t >= "14:00":
            slots.append(t)
        current += timedelta(minutes=minutes)

    return slots


def get_available_slots(doctor_id, selected_date, doctors, appointments):
    all_slots = get_doctor_slots(doctor_id, selected_date, doctors)

    booked = appointments[
        (appointments["doctor_id"].astype(str) == str(doctor_id)) &
        (appointments["date"].astype(str) == str(selected_date)) &
        (appointments["status"].astype(str).str.lower().isin(["booked", "confirmed"]))
    ]["time"].astype(str).tolist()

    return [s for s in all_slots if s not in booked]


def filter_slots(slots, period):
    if period == "morning":
        return [s for s in slots if int(s.split(":")[0]) < 13]
    if period == "afternoon":
        return [s for s in slots if int(s.split(":")[0]) >= 14]
    return slots


def send_whatsapp_message(to, msg):
    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "text",
        "text": {"body": msg}
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    r = requests.post(url, headers=headers, json=payload)
    print("SEND RESPONSE:", r.status_code, r.text)


def send_whatsapp_slot_list(to, slots):
    rows = []

    for i, slot in enumerate(slots, start=1):
        rows.append({
            "id": f"slot_{i}",
            "title": to_am_pm(slot),
            "description": "Available appointment slot"
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Available Slots"},
            "body": {"text": "Please choose your appointment time:"},
            "footer": {"text": "ABC Clinic"},
            "action": {
                "button": "Choose Time",
                "sections": [{
                    "title": "Available Time Slots",
                    "rows": rows
                }]
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    r = requests.post(url, headers=headers, json=payload)
    print("LIST RESPONSE:", r.status_code, r.text)


def doctor_list_text(doctors):
    return "\n".join([
        f"{d['doctor_id']}. {d['name']} - {d['specialization']}"
        for _, d in doctors.iterrows()
    ])


def show_menu():
    return (
        "Welcome to ABC Clinic 👋\n\n"
        "How can we help you today?\n\n"
        "1. Book Appointment\n"
        "2. View Doctors\n"
        "3. Clinic Timings\n"
        "4. Cancel Appointment\n"
        "5. Reschedule Appointment"
    )


def active_appointments(phone, appointments):
    return appointments[
        (appointments["phone"].astype(str) == str(phone)) &
        (appointments["status"].astype(str).str.lower().isin(["booked", "confirmed"]))
    ]


def fallback_ai(msg):
    m = msg.lower()

    result = {
        "intent": "unknown",
        "doctor_id": "",
        "date_iso": "",
        "time_period": "",
        "slot_number": "",
        "patient_name": "",
        "cancel_confirmation": "",
        "doctor_specialization": ""
    }

    if m.strip().isdigit():
        result["slot_number"] = m.strip()

    if any(x in m for x in ["tooth", "teeth", "dental", "dentist", "cavity", "gum"]):
        result["intent"] = "book"
        result["doctor_id"] = "2"

    elif any(x in m for x in ["skin", "rash", "acne", "allergy", "pimple", "dermatologist"]):
        result["intent"] = "book"
        result["doctor_id"] = "1"

    elif any(x in m for x in ["fever", "cold", "cough", "headache", "body pain", "stomach"]):
        result["intent"] = "book"
        result["doctor_id"] = "3"

    elif any(x in m for x in ["book", "appointment", "consult", "doctor", "schedule"]):
        result["intent"] = "book"

    if "cancel" in m:
        result["intent"] = "cancel"

    if any(x in m for x in ["reschedule", "postpone", "change appointment", "move appointment"]):
        result["intent"] = "reschedule"

    if any(x in m for x in ["morning", "am"]):
        result["time_period"] = "morning"

    if any(x in m for x in ["afternoon", "evening", "pm"]):
        result["time_period"] = "afternoon"

    if "yes" in m:
        result["cancel_confirmation"] = "yes"

    if "no" in m or "keep" in m:
        result["cancel_confirmation"] = "no"

    return result


def ask_ai(message, doctors, step):
    fallback = fallback_ai(message)

    if not GEMINI_API_KEY:
        return fallback

    doctor_data = doctors[["doctor_id", "name", "specialization"]].to_dict(orient="records")

    prompt = f"""
You are an AI receptionist for ABC Clinic.

Today is {today_iso()}.
Current conversation step is: {step}

Available doctors:
{doctor_data}

User message:
"{message}"

Return ONLY JSON:
{{
  "intent": "book" | "cancel" | "reschedule" | "view_doctors" | "clinic_timings" | "doctor_query" | "small_talk" | "unknown",
  "doctor_id": "",
  "doctor_specialization": "",
  "date_iso": "",
  "time_period": "morning" | "afternoon" | "",
  "slot_number": "",
  "patient_name": "",
  "cancel_confirmation": "yes" | "no" | "",
  "answer": ""
}}

Rules:
- Skin/acne/rash/allergy/skin specialist -> doctor_id "1".
- Tooth pain/dental/dentist/cavity -> doctor_id "2".
- Fever/cough/cold/headache/body pain/general sickness -> doctor_id "3".
- If asking psychiatrist/cardiologist/orthopedic/gynecologist and not available, use doctor_query.
- Convert dates like tomorrow, next Monday, next week Tuesday into YYYY-MM-DD.
- If user says morning/AM, time_period morning.
- If user says afternoon/evening/PM, time_period afternoon.
- If user gives name during ask_name, put patient_name.
- If user selects slot number, put slot_number.
- If user confirms cancellation, put cancel_confirmation yes or no.
"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        r = requests.post(url, headers=headers, json=payload, timeout=20)
        data = r.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()

        ai = json.loads(text)
        print("AI RESPONSE:", ai)

        for key in fallback:
            if not ai.get(key) and fallback.get(key):
                ai[key] = fallback[key]

        return ai

    except Exception as e:
        print("GEMINI ERROR:", e)
        return fallback


def ask_next_missing_info(phone, index, patients, doctors, appointments, conversations):
    name = str(conversations.loc[index, "patient_name"]).strip()
    doctor_id = str(conversations.loc[index, "doctor_id"]).strip()
    selected_date = str(conversations.loc[index, "date"]).strip()
    period = str(conversations.loc[index, "time_period"]).strip()

    if not name:
        conversations.loc[index, "step"] = "ask_name"
        write_all_sheets(patients, doctors, appointments, conversations)
        return "Please enter your full name."

    if not doctor_id:
        conversations.loc[index, "step"] = "ask_doctor"
        write_all_sheets(patients, doctors, appointments, conversations)
        return "Which doctor would you like to consult?\n\n" + doctor_list_text(doctors)

    doctor = doctors[doctors["doctor_id"].astype(str) == doctor_id].iloc[0]

    if not selected_date or not is_valid_date(selected_date):
        conversations.loc[index, "step"] = "ask_date"
        write_all_sheets(patients, doctors, appointments, conversations)
        return (
            f"Perfect. {doctor['name']} ({doctor['specialization']}) is selected.\n\n"
            "Please tell me the appointment date.\n"
            "Example: tomorrow / next Monday / 2026-06-10"
        )

    if not period:
        conversations.loc[index, "step"] = "ask_period"
        write_all_sheets(patients, doctors, appointments, conversations)
        return "Which time do you prefer?\n\n1. Morning\n2. Afternoon"

    available = get_available_slots(doctor_id, selected_date, doctors, appointments)
    filtered = filter_slots(available, period)

    if not filtered:
        conversations.loc[index, "time_period"] = ""
        conversations.loc[index, "step"] = "ask_period"
        write_all_sheets(patients, doctors, appointments, conversations)
        return "No slots available for that time period. Please choose:\n\n1. Morning\n2. Afternoon"

    conversations.loc[index, "step"] = "choose_slot"
    write_all_sheets(patients, doctors, appointments, conversations)

    send_whatsapp_slot_list(phone, filtered)
    return "Please tap Choose Time and select your preferred slot."


def process_chat_message(phone, message):
    message = str(message).strip()
    lower = message.lower()

    patients, doctors, appointments, conversations = load_all()

    existing = conversations[conversations["phone"].astype(str) == str(phone)]

    if existing.empty:
        conversations = pd.concat([conversations, pd.DataFrame([{
            "phone": phone,
            "patient_name": "",
            "step": "menu",
            "doctor_id": "",
            "date": "",
            "time_period": "",
            "appointment_id": ""
        }], dtype=object)], ignore_index=True)

        write_all_sheets(patients, doctors, appointments, conversations)
        patients, doctors, appointments, conversations = load_all()
        existing = conversations[conversations["phone"].astype(str) == str(phone)]

    index = existing.index[0]
    step = str(conversations.loc[index, "step"])

    ai = ask_ai(message, doctors, step)

    intent = ai.get("intent", "unknown")
    doctor_id = str(ai.get("doctor_id", "")).strip()
    date_iso = str(ai.get("date_iso", "")).strip()
    period = str(ai.get("time_period", "")).strip()
    slot_number = str(ai.get("slot_number", "")).strip()
    patient_name = str(ai.get("patient_name", "")).strip()
    cancel_confirmation = str(ai.get("cancel_confirmation", "")).strip()
    doctor_specialization = str(ai.get("doctor_specialization", "")).strip()

    if lower in ["hi", "hello", "menu", "start"]:
        conversations.loc[index, "step"] = "menu"
        write_all_sheets(patients, doctors, appointments, conversations)
        return show_menu()

    if step == "confirm_cancel":
        appointment_id = str(conversations.loc[index, "appointment_id"])

        if cancel_confirmation == "yes" or message == "1":
            selected = appointments[appointments["appointment_id"].astype(str) == appointment_id]

            if selected.empty:
                conversations.loc[index, "step"] = "menu"
                write_all_sheets(patients, doctors, appointments, conversations)
                return "Appointment not found."

            appt = selected.iloc[0]

            appointments.loc[
                appointments["appointment_id"].astype(str) == appointment_id,
                "status"
            ] = "cancelled"

            conversations.loc[index, "step"] = "menu"
            conversations.loc[index, "appointment_id"] = ""

            write_all_sheets(patients, doctors, appointments, conversations)

            return (
                "Your appointment has been cancelled ❌\n\n"
                f"Doctor: {appt['doctor_name']}\n"
                f"Date: {appt['date']}\n"
                f"Time: {to_am_pm(appt['time'])}"
            )

        if cancel_confirmation == "no" or message == "2":
            conversations.loc[index, "step"] = "completed"
            write_all_sheets(patients, doctors, appointments, conversations)
            return "Okay, your appointment remains booked ✅"

        return "Please confirm:\n\n1. Yes, cancel appointment\n2. No, keep appointment"

    if intent == "cancel":
        active = active_appointments(phone, appointments)

        if active.empty:
            conversations.loc[index, "step"] = "menu"
            write_all_sheets(patients, doctors, appointments, conversations)
            return "You do not have any active appointment to cancel."

        appt = active.iloc[-1]

        conversations.loc[index, "step"] = "confirm_cancel"
        conversations.loc[index, "appointment_id"] = str(appt["appointment_id"])
        write_all_sheets(patients, doctors, appointments, conversations)

        return (
            "Are you sure you want to cancel this appointment?\n\n"
            f"Doctor: {appt['doctor_name']}\n"
            f"Date: {appt['date']}\n"
            f"Time: {to_am_pm(appt['time'])}\n\n"
            "Reply:\n1. Yes, cancel appointment\n2. No, keep appointment"
        )

    if intent == "reschedule":
        active = active_appointments(phone, appointments)

        if active.empty:
            return "You do not have any active appointment to reschedule."

        appt = active.iloc[-1]

        conversations.loc[index, "step"] = "booking"
        conversations.loc[index, "appointment_id"] = str(appt["appointment_id"])
        conversations.loc[index, "patient_name"] = str(appt["patient_name"])
        conversations.loc[index, "doctor_id"] = str(appt["doctor_id"])
        conversations.loc[index, "date"] = ""
        conversations.loc[index, "time_period"] = ""

        write_all_sheets(patients, doctors, appointments, conversations)

        return (
            "Okay, let's reschedule your appointment.\n\n"
            f"Current appointment:\n"
            f"Doctor: {appt['doctor_name']}\n"
            f"Date: {appt['date']}\n"
            f"Time: {to_am_pm(appt['time'])}\n\n"
            "Please tell me the new date."
        )

    if intent == "clinic_timings" or (step == "menu" and message == "3"):
        return "ABC Clinic Timings:\n\nMonday to Saturday\n9:00 AM to 6:00 PM\n\nLunch: 1:00 PM to 2:00 PM\nSunday closed."

    if intent == "view_doctors" or (step == "menu" and message == "2"):
        return "Our doctors are:\n\n" + doctor_list_text(doctors)

    if intent == "doctor_query" and not doctor_id:
        return (
            f"I'm sorry, we do not currently have a {doctor_specialization or 'doctor for that specialty'} available.\n\n"
            "Available doctors are:\n\n"
            + doctor_list_text(doctors)
            + "\n\nWould you like to continue with one of these doctors?"
        )

    if intent == "book" or step in ["booking", "ask_name", "ask_doctor", "ask_date", "ask_period", "choose_slot"] or (step == "menu" and message == "1"):
        conversations.loc[index, "step"] = "booking"

        if patient_name:
            conversations.loc[index, "patient_name"] = patient_name
        elif step == "ask_name":
            conversations.loc[index, "patient_name"] = message

        if doctor_id:
            conversations.loc[index, "doctor_id"] = doctor_id
        elif step == "ask_doctor" and message in doctors["doctor_id"].astype(str).tolist():
            conversations.loc[index, "doctor_id"] = message

        if date_iso and is_valid_date(date_iso):
            conversations.loc[index, "date"] = date_iso
        elif step == "ask_date" and is_valid_date(message):
            conversations.loc[index, "date"] = message

        if period in ["morning", "afternoon"]:
            conversations.loc[index, "time_period"] = period
        elif step == "ask_period":
            if message == "1" or "morning" in lower:
                conversations.loc[index, "time_period"] = "morning"
            elif message == "2" or "afternoon" in lower:
                conversations.loc[index, "time_period"] = "afternoon"

        write_all_sheets(patients, doctors, appointments, conversations)
        patients, doctors, appointments, conversations = load_all()
        index = conversations[conversations["phone"].astype(str) == str(phone)].index[0]

        if step == "choose_slot":
            doctor_id = str(conversations.loc[index, "doctor_id"])
            selected_date = str(conversations.loc[index, "date"])
            period = str(conversations.loc[index, "time_period"])

            available = get_available_slots(doctor_id, selected_date, doctors, appointments)
            filtered = filter_slots(available, period)

            choice_text = slot_number if slot_number else message

            try:
                choice = int(choice_text)
            except:
                return "Please select a slot from the list."

            if choice < 1 or choice > len(filtered):
                return "Please choose a valid slot."

            selected_time = filtered[choice - 1]
            patient = str(conversations.loc[index, "patient_name"])
            doc = doctors[doctors["doctor_id"].astype(str) == doctor_id].iloc[0]

            appointment_id = str(conversations.loc[index, "appointment_id"])

            if appointment_id:
                appointments.loc[
                    appointments["appointment_id"].astype(str) == appointment_id,
                    "date"
                ] = selected_date

                appointments.loc[
                    appointments["appointment_id"].astype(str) == appointment_id,
                    "time"
                ] = selected_time

                appointments.loc[
                    appointments["appointment_id"].astype(str) == appointment_id,
                    "status"
                ] = "booked"

                msg_start = "Appointment rescheduled successfully ✅"
            else:
                appointment_id = next_id(appointments, "appointment_id")

                appointments = pd.concat([appointments, pd.DataFrame([{
                    "appointment_id": appointment_id,
                    "patient_name": patient,
                    "phone": phone,
                    "doctor_id": doctor_id,
                    "doctor_name": doc["name"],
                    "date": selected_date,
                    "time": selected_time,
                    "status": "booked",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "reminder_sent": "False"
                }], dtype=object)], ignore_index=True)

                msg_start = "Appointment successfully booked ✅"

            if phone not in patients["phone"].astype(str).values:
                patients = pd.concat([patients, pd.DataFrame([{
                    "patient_id": next_id(patients, "patient_id"),
                    "name": patient,
                    "phone": phone
                }], dtype=object)], ignore_index=True)

            conversations.loc[index, "step"] = "completed"
            conversations.loc[index, "appointment_id"] = appointment_id

            write_all_sheets(patients, doctors, appointments, conversations)

            return (
                f"{msg_start}\n\n"
                f"Patient: {patient}\n"
                f"Doctor: {doc['name']}\n"
                f"Date: {selected_date}\n"
                f"Time: {to_am_pm(selected_time)}\n\n"
                "ABC Clinic will confirm your appointment soon."
            )

        return ask_next_missing_info(phone, index, patients, doctors, appointments, conversations)

    if step == "completed":
        return "You already have a booking.\n\nYou can type:\n• book another appointment\n• cancel appointment\n• reschedule appointment\n• menu"

    return show_menu()


@app.route("/")
def home():
    return jsonify({"message": "Clinic Pro WhatsApp Backend is running"})


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200

    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def receive_webhook():
    data = request.get_json()
    print("WHATSAPP WEBHOOK DATA:", data)

    try:
        value = data["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])

        if messages:
            phone = messages[0]["from"]
            text = ""

            if "text" in messages[0]:
                text = messages[0]["text"]["body"]

            elif "interactive" in messages[0]:
                interactive = messages[0]["interactive"]
                if interactive["type"] == "list_reply":
                    text = interactive["list_reply"]["id"].replace("slot_", "")

            if text:
                reply = process_chat_message(phone, text)
                send_whatsapp_message(phone, reply)

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return "EVENT_RECEIVED", 200


@app.route("/doctors", methods=["GET"])
def get_doctors():
    _, doctors, _, _ = load_all()
    return jsonify(doctors.to_dict(orient="records"))


@app.route("/appointments", methods=["GET"])
def get_appointments():
    _, _, appointments, _ = load_all()
    return jsonify(appointments.to_dict(orient="records"))


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    reply = process_chat_message(str(data.get("phone", "")), str(data.get("message", "")))
    return jsonify({"reply": reply})


@app.route("/add-doctor", methods=["POST"])
def add_doctor():
    data = request.json
    patients, doctors, appointments, conversations = load_all()

    new_doctor = {
        "doctor_id": next_id(doctors, "doctor_id"),
        "name": str(data.get("name", "")).strip(),
        "specialization": str(data.get("specialization", "")).strip(),
        "working_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
        "start_time": "09:00",
        "end_time": "18:00",
        "slot_minutes": "30"
    }

    if not new_doctor["name"] or not new_doctor["specialization"]:
        return jsonify({"success": False, "message": "Doctor name and specialization required"}), 400

    doctors = pd.concat([doctors, pd.DataFrame([new_doctor], dtype=object)], ignore_index=True)
    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({"success": True, "doctor": new_doctor})


@app.route("/delete-doctor/<doctor_id>", methods=["POST"])
def delete_doctor(doctor_id):
    patients, doctors, appointments, conversations = load_all()

    active = appointments[
        (appointments["doctor_id"].astype(str) == str(doctor_id)) &
        (appointments["status"].astype(str).str.lower().isin(["booked", "confirmed"]))
    ]

    if not active.empty:
        return jsonify({"success": False, "message": "Cannot delete doctor with active appointments"}), 409

    doctors = doctors[doctors["doctor_id"].astype(str) != str(doctor_id)]
    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({"success": True})


@app.route("/confirm/<int:appointment_id>", methods=["POST"])
def confirm_appointment(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    selected = appointments[appointments["appointment_id"].astype(str) == str(appointment_id)]
    if selected.empty:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    appt = selected.iloc[0]

    appointments.loc[
        appointments["appointment_id"].astype(str) == str(appointment_id),
        "status"
    ] = "confirmed"

    write_all_sheets(patients, doctors, appointments, conversations)

    msg = (
        "Your appointment has been confirmed ✅\n\n"
        f"Patient: {appt['patient_name']}\n"
        f"Doctor: {appt['doctor_name']}\n"
        f"Date: {appt['date']}\n"
        f"Time: {to_am_pm(appt['time'])}"
    )

    send_whatsapp_message(appt["phone"], msg)

    return jsonify({"success": True, "whatsapp_message": msg})


@app.route("/cancel/<int:appointment_id>", methods=["POST"])
def cancel_appointment(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    appointments.loc[
        appointments["appointment_id"].astype(str) == str(appointment_id),
        "status"
    ] = "cancelled"

    write_all_sheets(patients, doctors, appointments, conversations)
    return jsonify({"success": True})


@app.route("/reminder/<int:appointment_id>", methods=["POST"])
def reminder(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    selected = appointments[appointments["appointment_id"].astype(str) == str(appointment_id)]
    if selected.empty:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    appt = selected.iloc[0]

    msg = (
        "Reminder from ABC Clinic ⏰\n\n"
        f"Hi {appt['patient_name']},\n"
        f"Your appointment with {appt['doctor_name']} is on {appt['date']} at {to_am_pm(appt['time'])}."
    )

    send_whatsapp_message(appt["phone"], msg)
    return jsonify({"success": True, "whatsapp_message": msg})


@app.route("/reset-chat/<phone>", methods=["POST", "GET"])
def reset_chat(phone):
    patients, doctors, appointments, conversations = load_all()
    conversations = conversations[conversations["phone"].astype(str) != str(phone)]
    write_all_sheets(patients, doctors, appointments, conversations)
    return jsonify({"success": True, "message": "Chat reset successfully"})


if __name__ == "__main__":
    create_excel_if_missing()
    app.run(debug=True, port=5055)# force redeploy
