from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import os
from datetime import datetime, date, timedelta
import re
import requests
import json

app = Flask(__name__)
CORS(app)

EXCEL_FILE = "database.xlsx"
VERIFY_TOKEN = "clinic_verify_123"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-2.0-flash"


def to_am_pm(time_text):
    return datetime.strptime(str(time_text), "%H:%M").strftime("%I:%M %p")


def is_valid_date(date_text):
    try:
        selected = datetime.strptime(str(date_text), "%Y-%m-%d").date()
        return selected >= date.today()
    except ValueError:
        return False


def create_excel_if_missing():
    if not os.path.exists(EXCEL_FILE):
        patients = pd.DataFrame({
            "patient_id": pd.Series(dtype="object"),
            "name": pd.Series(dtype="object"),
            "phone": pd.Series(dtype="object")
        })

        doctors = pd.DataFrame([
            {
                "doctor_id": "1",
                "name": "Dr Priya",
                "specialization": "Dermatologist",
                "working_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
                "start_time": "09:00",
                "end_time": "18:00",
                "slot_minutes": "30"
            },
            {
                "doctor_id": "2",
                "name": "Dr Kumar",
                "specialization": "Dentist",
                "working_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
                "start_time": "09:00",
                "end_time": "18:00",
                "slot_minutes": "30"
            },
            {
                "doctor_id": "3",
                "name": "Dr Mehta",
                "specialization": "General Physician",
                "working_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
                "start_time": "09:00",
                "end_time": "18:00",
                "slot_minutes": "30"
            },
        ], dtype=object)

        appointments = pd.DataFrame({
            "appointment_id": pd.Series(dtype="object"),
            "patient_name": pd.Series(dtype="object"),
            "phone": pd.Series(dtype="object"),
            "doctor_id": pd.Series(dtype="object"),
            "doctor_name": pd.Series(dtype="object"),
            "date": pd.Series(dtype="object"),
            "time": pd.Series(dtype="object"),
            "status": pd.Series(dtype="object"),
            "created_at": pd.Series(dtype="object"),
            "reminder_sent": pd.Series(dtype="object")
        })

        conversations = pd.DataFrame({
            "phone": pd.Series(dtype="object"),
            "patient_name": pd.Series(dtype="object"),
            "step": pd.Series(dtype="object"),
            "verified": pd.Series(dtype="object"),
            "doctor_id": pd.Series(dtype="object"),
            "date": pd.Series(dtype="object"),
            "time": pd.Series(dtype="object"),
            "appointment_id": pd.Series(dtype="object")
        })

        write_all_sheets(patients, doctors, appointments, conversations)


def read_sheet(sheet_name):
    create_excel_if_missing()
    try:
        df = pd.read_excel(EXCEL_FILE, sheet_name=sheet_name, dtype=object)
        df = df.fillna("")
        return df.astype(object)
    except Exception:
        return pd.DataFrame()


def ensure_columns(df, columns):
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df.astype(object)


def load_all():
    patients = ensure_columns(read_sheet("patients"), ["patient_id", "name", "phone"])

    doctors = ensure_columns(read_sheet("doctors"), [
        "doctor_id", "name", "specialization",
        "working_days", "start_time", "end_time", "slot_minutes"
    ])

    for i in doctors.index:
        if not str(doctors.loc[i, "working_days"]).strip():
            doctors.loc[i, "working_days"] = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
        if not str(doctors.loc[i, "start_time"]).strip():
            doctors.loc[i, "start_time"] = "09:00"
        if not str(doctors.loc[i, "end_time"]).strip():
            doctors.loc[i, "end_time"] = "18:00"
        if not str(doctors.loc[i, "slot_minutes"]).strip():
            doctors.loc[i, "slot_minutes"] = "30"

    appointments = ensure_columns(read_sheet("appointments"), [
        "appointment_id", "patient_name", "phone", "doctor_id",
        "doctor_name", "date", "time", "status", "created_at", "reminder_sent"
    ])

    conversations = ensure_columns(read_sheet("conversations"), [
        "phone", "patient_name", "step", "verified",
        "doctor_id", "date", "time", "appointment_id"
    ])

    return patients, doctors, appointments, conversations


def write_all_sheets(patients, doctors, appointments, conversations):
    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
        patients.astype(object).to_excel(writer, sheet_name="patients", index=False)
        doctors.astype(object).to_excel(writer, sheet_name="doctors", index=False)
        appointments.astype(object).to_excel(writer, sheet_name="appointments", index=False)
        conversations.astype(object).to_excel(writer, sheet_name="conversations", index=False)


def next_id(df, column):
    if df.empty:
        return "1"
    nums = pd.to_numeric(df[column], errors="coerce").fillna(0)
    return str(int(nums.max()) + 1)


def get_doctor_slots(doctor_id, selected_date, doctors):
    doctor = doctors[doctors["doctor_id"].astype(str) == str(doctor_id)]

    if doctor.empty:
        return []

    doctor = doctor.iloc[0]
    selected_day = datetime.strptime(selected_date, "%Y-%m-%d").strftime("%A")
    working_days = [d.strip() for d in str(doctor["working_days"]).split(",")]

    if selected_day not in working_days:
        return []

    start_time = str(doctor["start_time"])
    end_time = str(doctor["end_time"])
    slot_minutes = int(str(doctor["slot_minutes"] or "30"))

    slots = []
    current = datetime.strptime(start_time, "%H:%M")
    end = datetime.strptime(end_time, "%H:%M")

    while current < end:
        time_value = current.strftime("%H:%M")

        if time_value < "13:00" or time_value >= "14:00":
            slots.append(time_value)

        current += timedelta(minutes=slot_minutes)

    return slots


def get_available_slots(doctor_id, selected_date, doctors, appointments):
    all_slots = get_doctor_slots(doctor_id, selected_date, doctors)

    if appointments.empty:
        return all_slots

    booked_slots = appointments[
        (appointments["doctor_id"].astype(str) == str(doctor_id)) &
        (appointments["date"].astype(str) == str(selected_date)) &
        (appointments["status"].astype(str).str.lower().isin(["booked", "confirmed"]))
    ]["time"].astype(str).tolist()

    return [slot for slot in all_slots if slot not in booked_slots]


def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "text",
        "text": {"body": message}
    }

    response = requests.post(url, headers=headers, json=payload)
    print("SEND RESPONSE:", response.status_code)
    print(response.text)


def show_menu():
    return (
        "Welcome to ABC Clinic 👋\n\n"
        "You can type naturally, for example:\n"
        "• I need a skin specialist\n"
        "• I have tooth pain\n"
        "• Book appointment tomorrow\n"
        "• Cancel my appointment\n"
        "• Reschedule my appointment\n\n"
        "Or choose:\n"
        "1. Book Appointment\n"
        "2. View Doctors\n"
        "3. Clinic Timings"
    )


def doctor_list_text(doctors):
    lines = []
    for _, doctor in doctors.iterrows():
        lines.append(f"{doctor['doctor_id']}. {doctor['name']} - {doctor['specialization']}")
    return "\n".join(lines)


def active_appointments_for_phone(phone, appointments):
    return appointments[
        (appointments["phone"].astype(str) == str(phone)) &
        (appointments["status"].astype(str).str.lower().isin(["booked", "confirmed"]))
    ]


def ask_gemini(user_message, doctors, step):
    doctor_info = doctors[["doctor_id", "name", "specialization"]].to_dict(orient="records")
    today = date.today().strftime("%Y-%m-%d")

    prompt = f"""
You are an AI receptionist for ABC Clinic.

Today is {today}.

Available doctors:
{doctor_info}

Current booking step:
{step}

User message:
"{user_message}"

Understand the patient's intent and return ONLY valid JSON.

JSON format:
{{
  "intent": "book" | "cancel" | "reschedule" | "view_doctors" | "clinic_timings" | "doctor_query" | "small_talk" | "unknown",
  "doctor_id": "",
  "doctor_specialization": "",
  "date_iso": "",
  "patient_name": "",
  "slot_number": "",
  "answer": ""
}}

Rules:
- If user says skin specialist, skin rash, acne, allergy, pigmentation, dermatologist → doctor_id should be "1".
- If user says tooth pain, dental, teeth, dentist, cavity → doctor_id should be "2".
- If user says fever, cough, cold, headache, body pain, general sickness → doctor_id should be "3".
- If user asks for psychiatrist, cardiologist, orthopedist, gynecologist, etc. and it is not in the available doctors, intent should be "doctor_query" and doctor_id should be "".
- If user wants to book, schedule, see doctor, consult, get appointment → intent "book".
- If user wants cancel/remove booking → intent "cancel".
- If user wants reschedule/change/postpone appointment → intent "reschedule".
- Convert dates like tomorrow, next Monday, next week Tuesday, 3rd of next month into YYYY-MM-DD in date_iso.
- If user gives a name during ask_name step, put it in patient_name.
- If user chooses slot number like 1, 2, 3, put it in slot_number.
- Do not invent doctors.
- Keep answer short and clinic-friendly.
"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY
        }

        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }

        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()

        ai = json.loads(text)
        print("AI RESPONSE:", ai)
        return ai

    except Exception as e:
        print("GEMINI ERROR:", e)
        return {
            "intent": "unknown",
            "doctor_id": "",
            "doctor_specialization": "",
            "date_iso": "",
            "patient_name": "",
            "slot_number": "",
            "answer": ""
        }


def choose_doctor_response(doctor_id, doctors):
    doctor_row = doctors[doctors["doctor_id"].astype(str) == str(doctor_id)]

    if doctor_row.empty:
        return None

    doctor = doctor_row.iloc[0]

    return (
        f"Perfect. Based on your message, {doctor['name']} "
        f"({doctor['specialization']}) is the right doctor.\n\n"
        "Please enter appointment date in YYYY-MM-DD format.\n"
        "Example: 2026-06-10"
    )


def send_available_slots_response(doctor_id, selected_date, doctors, appointments):
    available_slots = get_available_slots(doctor_id, selected_date, doctors, appointments)

    if not available_slots:
        return "No slots available on this date. Please enter another date."

    slot_lines = [
        f"{i}. {to_am_pm(slot)}"
        for i, slot in enumerate(available_slots, start=1)
    ]

    return (
        "Available slots:\n\n"
        + "\n".join(slot_lines)
        + "\n\nPlease reply with the slot number."
    )


def process_chat_message(phone, message):
    message = str(message).strip()
    lower_msg = message.lower()

    patients, doctors, appointments, conversations = load_all()

    existing = conversations[conversations["phone"].astype(str) == str(phone)]

    if existing.empty:
        conversations = pd.concat([conversations, pd.DataFrame([{
            "phone": phone,
            "patient_name": "",
            "step": "menu",
            "verified": "True",
            "doctor_id": "",
            "date": "",
            "time": "",
            "appointment_id": ""
        }], dtype=object)], ignore_index=True)

        write_all_sheets(patients, doctors, appointments, conversations)

        patients, doctors, appointments, conversations = load_all()
        existing = conversations[conversations["phone"].astype(str) == str(phone)]
    
    index = existing.index[0]
    step = str(conversations.loc[index, "step"])

    ai = ask_gemini(message, doctors, step)
    intent = ai.get("intent", "unknown")
    ai_doctor_id = str(ai.get("doctor_id", "")).strip()
    ai_date = str(ai.get("date_iso", "")).strip()
    ai_slot_number = str(ai.get("slot_number", "")).strip()
    ai_patient_name = str(ai.get("patient_name", "")).strip()
    ai_specialization = str(ai.get("doctor_specialization", "")).strip()

    if lower_msg in ["hi", "hello", "menu", "start"]:
        conversations.loc[index, "step"] = "menu"
        write_all_sheets(patients, doctors, appointments, conversations)
        return show_menu()

    if intent == "cancel" or "cancel" in lower_msg:
        active = active_appointments_for_phone(phone, appointments)

        if active.empty:
            conversations.loc[index, "step"] = "menu"
            write_all_sheets(patients, doctors, appointments, conversations)
            return "You do not have any active appointment to cancel."

        appt = active.iloc[-1]

        appointments.loc[
            appointments["appointment_id"].astype(str) == str(appt["appointment_id"]),
            "status"
        ] = "cancelled"

        conversations.loc[index, "step"] = "menu"

        write_all_sheets(patients, doctors, appointments, conversations)

        return (
            "Your appointment has been cancelled ❌\n\n"
            f"Doctor: {appt['doctor_name']}\n"
            f"Date: {appt['date']}\n"
            f"Time: {to_am_pm(appt['time'])}\n\n"
            "Type 'book appointment' if you want a new appointment."
        )

    if intent == "reschedule" or "reschedule" in lower_msg or "change" in lower_msg or "postpone" in lower_msg:
        active = active_appointments_for_phone(phone, appointments)

        if active.empty:
            return "You do not have any active appointment to reschedule."

        appt = active.iloc[-1]

        conversations.loc[index, "appointment_id"] = str(appt["appointment_id"])
        conversations.loc[index, "doctor_id"] = str(appt["doctor_id"])
        conversations.loc[index, "step"] = "reschedule_date"

        write_all_sheets(patients, doctors, appointments, conversations)

        return (
            "Okay, let's reschedule your appointment.\n\n"
            f"Current appointment:\n"
            f"Doctor: {appt['doctor_name']}\n"
            f"Date: {appt['date']}\n"
            f"Time: {to_am_pm(appt['time'])}\n\n"
            "Please enter the new date."
        )

    if intent == "clinic_timings" or message == "3":
        return (
            "ABC Clinic Timings:\n\n"
            "Monday to Saturday\n"
            "9:00 AM to 6:00 PM\n\n"
            "Lunch Break: 1:00 PM to 2:00 PM\n"
            "Sunday closed."
        )

    if intent == "view_doctors" or message == "2":
        return "Our doctors are:\n\n" + doctor_list_text(doctors)

    if intent == "doctor_query" and not ai_doctor_id:
        return (
            f"I'm sorry, we do not currently have a {ai_specialization or 'doctor for that specialty'} available.\n\n"
            "Available doctors are:\n\n"
            + doctor_list_text(doctors)
            + "\n\nWould you like to continue with one of these doctors?\nReply with the doctor number or type cancel."
        )

    if step == "menu":
        if message == "1" or intent == "book":
            conversations.loc[index, "step"] = "ask_name"

            if ai_doctor_id:
                conversations.loc[index, "doctor_id"] = ai_doctor_id

            if ai_date and is_valid_date(ai_date):
                conversations.loc[index, "date"] = ai_date

            write_all_sheets(patients, doctors, appointments, conversations)

            if ai_doctor_id:
                doctor_row = doctors[doctors["doctor_id"].astype(str) == ai_doctor_id].iloc[0]
                return (
                    f"Sure. I can help you book with {doctor_row['name']} "
                    f"({doctor_row['specialization']}).\n\n"
                    "Please enter your full name."
                )

            return "Sure, I can help you book an appointment. Please enter your full name."

        return show_menu()

    if step == "ask_name":
        patient_name = ai_patient_name if ai_patient_name else message

        conversations.loc[index, "patient_name"] = patient_name

        existing_doctor_id = str(conversations.loc[index, "doctor_id"]).strip()
        existing_date = str(conversations.loc[index, "date"]).strip()

        if ai_doctor_id:
            conversations.loc[index, "doctor_id"] = ai_doctor_id
            existing_doctor_id = ai_doctor_id

        if ai_date and is_valid_date(ai_date):
            conversations.loc[index, "date"] = ai_date
            existing_date = ai_date

        if existing_doctor_id:
            conversations.loc[index, "step"] = "ask_date"
            write_all_sheets(patients, doctors, appointments, conversations)

            if existing_date and is_valid_date(existing_date):
                conversations.loc[index, "step"] = "choose_slot"
                write_all_sheets(patients, doctors, appointments, conversations)
                return send_available_slots_response(existing_doctor_id, existing_date, doctors, appointments)

            return choose_doctor_response(existing_doctor_id, doctors)

        conversations.loc[index, "step"] = "ask_doctor"
        write_all_sheets(patients, doctors, appointments, conversations)

        return "Choose doctor:\n\n" + doctor_list_text(doctors)

    if step == "ask_doctor":
        selected_doctor_id = ""

        if message in doctors["doctor_id"].astype(str).tolist():
            selected_doctor_id = message
        elif ai_doctor_id:
            selected_doctor_id = ai_doctor_id

        if not selected_doctor_id:
            return (
                "I couldn't match that to an available doctor.\n\n"
                "Available doctors are:\n\n"
                + doctor_list_text(doctors)
                + "\n\nPlease reply with the doctor number."
            )

        conversations.loc[index, "doctor_id"] = selected_doctor_id
        conversations.loc[index, "step"] = "ask_date"

        write_all_sheets(patients, doctors, appointments, conversations)

        return choose_doctor_response(selected_doctor_id, doctors)

    if step == "ask_date":
        selected_date = ""

        if is_valid_date(message):
            selected_date = message
        elif ai_date and is_valid_date(ai_date):
            selected_date = ai_date

        if not selected_date:
            return (
                "Please enter a valid future date.\n\n"
                "Examples:\n"
                "2026-06-10\n"
                "tomorrow\n"
                "next Tuesday"
            )

        doctor_id = str(conversations.loc[index, "doctor_id"])

        conversations.loc[index, "date"] = selected_date
        conversations.loc[index, "step"] = "choose_slot"

        write_all_sheets(patients, doctors, appointments, conversations)

        return send_available_slots_response(doctor_id, selected_date, doctors, appointments)

    if step == "choose_slot":
        doctor_id = str(conversations.loc[index, "doctor_id"])
        selected_date = str(conversations.loc[index, "date"])

        available_slots = get_available_slots(doctor_id, selected_date, doctors, appointments)

        slot_text = ai_slot_number if ai_slot_number else message

        try:
            choice = int(slot_text)
        except ValueError:
            return "Please enter the slot number shown in the list."

        if choice < 1 or choice > len(available_slots):
            return "Please choose a valid slot number."

        selected_time = available_slots[choice - 1]
        patient_name = str(conversations.loc[index, "patient_name"])

        doctor_row = doctors[doctors["doctor_id"].astype(str) == doctor_id].iloc[0]
        doctor_name = doctor_row["name"]

        new_appointment = {
            "appointment_id": next_id(appointments, "appointment_id"),
            "patient_name": patient_name,
            "phone": phone,
            "doctor_id": doctor_id,
            "doctor_name": doctor_name,
            "date": selected_date,
            "time": selected_time,
            "status": "booked",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reminder_sent": "False"
        }

        appointments = pd.concat(
            [appointments, pd.DataFrame([new_appointment], dtype=object)],
            ignore_index=True
        )

        if phone not in patients["phone"].astype(str).values:
            patients = pd.concat([patients, pd.DataFrame([{
                "patient_id": next_id(patients, "patient_id"),
                "name": patient_name,
                "phone": phone
            }], dtype=object)], ignore_index=True)

        conversations.loc[index, "step"] = "completed"
        conversations.loc[index, "time"] = selected_time
        conversations.loc[index, "appointment_id"] = new_appointment["appointment_id"]

        write_all_sheets(patients, doctors, appointments, conversations)

        return (
            "Appointment successfully booked ✅\n\n"
            f"Patient: {patient_name}\n"
            f"Doctor: {doctor_name}\n"
            f"Date: {selected_date}\n"
            f"Time: {to_am_pm(selected_time)}\n\n"
            "ABC Clinic will confirm your appointment soon.\n\n"
            "You can type 'cancel appointment' or 'reschedule appointment' anytime."
        )

    if step == "reschedule_date":
        selected_date = ""

        if is_valid_date(message):
            selected_date = message
        elif ai_date and is_valid_date(ai_date):
            selected_date = ai_date

        if not selected_date:
            return "Please enter a valid future date."

        doctor_id = str(conversations.loc[index, "doctor_id"])

        conversations.loc[index, "date"] = selected_date
        conversations.loc[index, "step"] = "reschedule_slot"

        write_all_sheets(patients, doctors, appointments, conversations)

        return send_available_slots_response(doctor_id, selected_date, doctors, appointments)

    if step == "reschedule_slot":
        doctor_id = str(conversations.loc[index, "doctor_id"])
        selected_date = str(conversations.loc[index, "date"])
        appointment_id = str(conversations.loc[index, "appointment_id"])

        available_slots = get_available_slots(doctor_id, selected_date, doctors, appointments)

        slot_text = ai_slot_number if ai_slot_number else message

        try:
            choice = int(slot_text)
        except ValueError:
            return "Please enter the slot number shown in the list."

        if choice < 1 or choice > len(available_slots):
            return "Please choose a valid slot number."

        selected_time = available_slots[choice - 1]

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

        conversations.loc[index, "step"] = "completed"

        write_all_sheets(patients, doctors, appointments, conversations)

        return (
            "Appointment rescheduled successfully ✅\n\n"
            f"New Date: {selected_date}\n"
            f"New Time: {to_am_pm(selected_time)}"
        )

    if step == "completed":
        if intent == "book" or message == "1":
            conversations.loc[index, "step"] = "ask_name"
            conversations.loc[index, "patient_name"] = ""
            conversations.loc[index, "doctor_id"] = ai_doctor_id if ai_doctor_id else ""
            conversations.loc[index, "date"] = ai_date if ai_date else ""
            conversations.loc[index, "time"] = ""
            conversations.loc[index, "appointment_id"] = ""

            write_all_sheets(patients, doctors, appointments, conversations)

            return "Sure, let's book another appointment. Please enter your full name."

        return (
            "You already have a booking.\n\n"
            "You can type:\n"
            "• book another appointment\n"
            "• cancel appointment\n"
            "• reschedule appointment\n"
            "• menu"
        )

    return "Something went wrong. Type menu to start again."


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

            if "text" in messages[0]:
                text = messages[0]["text"]["body"]
                print("MESSAGE:", text)

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
    phone = str(data.get("phone", "")).strip()
    message = str(data.get("message", "")).strip()
    reply = process_chat_message(phone, message)
    return jsonify({"reply": reply})


@app.route("/add-doctor", methods=["POST"])
def add_doctor():
    data = request.json

    name = str(data.get("name", "")).strip()
    specialization = str(data.get("specialization", "")).strip()

    if not name or not specialization:
        return jsonify({"success": False, "message": "Doctor name and specialization required"}), 400

    patients, doctors, appointments, conversations = load_all()

    new_doctor = {
        "doctor_id": next_id(doctors, "doctor_id"),
        "name": name,
        "specialization": specialization,
        "working_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
        "start_time": "09:00",
        "end_time": "18:00",
        "slot_minutes": "30"
    }

    doctors = pd.concat([doctors, pd.DataFrame([new_doctor], dtype=object)], ignore_index=True)

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({"success": True, "message": "Doctor added", "doctor": new_doctor})


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

    return jsonify({"success": True, "message": "Doctor deleted"})


@app.route("/book", methods=["POST"])
def book_appointment():
    data = request.json

    patient_name = str(data.get("patient_name", "")).strip()
    phone = str(data.get("phone", "")).strip()
    doctor_id = str(data.get("doctor_id", "")).strip()
    selected_date = str(data.get("date", "")).strip()
    selected_time = str(data.get("time", "")).strip()

    if not patient_name or not phone or not doctor_id or not selected_date or not selected_time:
        return jsonify({"success": False, "message": "All fields are required"}), 400

    patients, doctors, appointments, conversations = load_all()

    available_slots = get_available_slots(doctor_id, selected_date, doctors, appointments)

    if selected_time not in available_slots:
        return jsonify({"success": False, "message": "This slot is not available"}), 409

    doctor_row = doctors[doctors["doctor_id"].astype(str) == doctor_id]

    if doctor_row.empty:
        return jsonify({"success": False, "message": "Doctor not found"}), 404

    doctor_name = doctor_row.iloc[0]["name"]

    new_appointment = {
        "appointment_id": next_id(appointments, "appointment_id"),
        "patient_name": patient_name,
        "phone": phone,
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "date": selected_date,
        "time": selected_time,
        "status": "booked",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reminder_sent": "False"
    }

    appointments = pd.concat([appointments, pd.DataFrame([new_appointment], dtype=object)], ignore_index=True)

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({"success": True, "message": "Appointment booked", "appointment": new_appointment})


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

    return jsonify({"success": True, "message": "Confirmed", "whatsapp_message": msg})


@app.route("/complete/<int:appointment_id>", methods=["POST"])
def complete_appointment(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    selected = appointments[appointments["appointment_id"].astype(str) == str(appointment_id)]

    if selected.empty:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    appt = selected.iloc[0]

    appointments.loc[
        appointments["appointment_id"].astype(str) == str(appointment_id),
        "status"
    ] = "completed"

    write_all_sheets(patients, doctors, appointments, conversations)

    msg = f"Thank you for visiting ABC Clinic, {appt['patient_name']} 😊"

    send_whatsapp_message(appt["phone"], msg)

    return jsonify({"success": True, "message": "Completed", "whatsapp_message": msg})


@app.route("/cancel/<int:appointment_id>", methods=["POST"])
def cancel_appointment(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    selected = appointments[appointments["appointment_id"].astype(str) == str(appointment_id)]

    if selected.empty:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    appt = selected.iloc[0]

    appointments.loc[
        appointments["appointment_id"].astype(str) == str(appointment_id),
        "status"
    ] = "cancelled"

    write_all_sheets(patients, doctors, appointments, conversations)

    msg = (
        "Your appointment has been cancelled ❌\n\n"
        f"Doctor: {appt['doctor_name']}\n"
        f"Date: {appt['date']}\n"
        f"Time: {to_am_pm(appt['time'])}"
    )

    send_whatsapp_message(appt["phone"], msg)

    return jsonify({"success": True, "message": "Cancelled", "whatsapp_message": msg})


@app.route("/reminder/<int:appointment_id>", methods=["POST"])
def send_reminder(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    selected = appointments[appointments["appointment_id"].astype(str) == str(appointment_id)]

    if selected.empty:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    appt = selected.iloc[0]

    msg = (
        "Reminder from ABC Clinic ⏰\n\n"
        f"Hi {appt['patient_name']},\n"
        f"Your appointment with {appt['doctor_name']} is on {appt['date']} at {to_am_pm(appt['time'])}.\n\n"
        "Please be on time."
    )

    send_whatsapp_message(appt["phone"], msg)

    return jsonify({"success": True, "message": "Reminder sent", "whatsapp_message": msg})


@app.route("/send-due-reminders", methods=["POST", "GET"])
def send_due_reminders():
    patients, doctors, appointments, conversations = load_all()

    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    count = 0

    for i, appt in appointments.iterrows():
        if (
            str(appt["date"]) == tomorrow and
            str(appt["status"]).lower() in ["booked", "confirmed"] and
            str(appt["reminder_sent"]).lower() != "true"
        ):
            msg = (
                "Reminder from ABC Clinic ⏰\n\n"
                f"Hi {appt['patient_name']},\n"
                f"Your appointment with {appt['doctor_name']} is tomorrow at {to_am_pm(appt['time'])}.\n\n"
                "Please be on time."
            )

            send_whatsapp_message(appt["phone"], msg)
            appointments.loc[i, "reminder_sent"] = "True"
            count += 1

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({"success": True, "reminders_sent": count})


@app.route("/reset-chat/<phone>", methods=["POST", "GET"])
def reset_chat(phone):
    patients, doctors, appointments, conversations = load_all()

    conversations = conversations[
        conversations["phone"].astype(str) != str(phone)
    ]

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({"success": True, "message": "Chat reset successfully"})


if __name__ == "__main__":
    create_excel_if_missing()
    app.run(debug=True, port=5055)