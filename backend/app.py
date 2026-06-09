from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
import os
import json
import requests

app = Flask(__name__)
CORS(app)

VERIFY_TOKEN = "clinic_verify_123"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///clinic_local.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), unique=True, nullable=False)


class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    specialization = db.Column(db.String(200), nullable=False)
    working_days = db.Column(db.String(300), default="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday")
    start_time = db.Column(db.String(20), default="09:00")
    end_time = db.Column(db.String(20), default="18:00")
    slot_minutes = db.Column(db.Integer, default=30)


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    doctor_name = db.Column(db.String(200), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    time = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(50), default="booked")
    created_at = db.Column(db.String(50), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    reminder_sent = db.Column(db.Boolean, default=False)


class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(50), unique=True, nullable=False)
    patient_name = db.Column(db.String(200), default="")
    step = db.Column(db.String(50), default="menu")
    doctor_id = db.Column(db.String(20), default="")
    date = db.Column(db.String(20), default="")
    time_period = db.Column(db.String(50), default="")
    appointment_id = db.Column(db.String(20), default="")


class ProcessedMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(200), unique=True, nullable=False)
    created_at = db.Column(db.String(50), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def init_db():
    db.create_all()

    if Doctor.query.count() == 0:
        db.session.add_all([
            Doctor(id=1, name="Dr Priya", specialization="Dermatologist"),
            Doctor(id=2, name="Dr Kumar", specialization="Dentist"),
            Doctor(id=3, name="Dr Mehta", specialization="General Physician"),
        ])
        db.session.commit()


with app.app_context():
    init_db()


def action(kind, text=""):
    return {"kind": kind, "text": text}


def to_am_pm(t):
    return datetime.strptime(str(t), "%H:%M").strftime("%I:%M %p")


def normalize_time_id(value):
    value = str(value).strip()

    if value.startswith("slot_"):
        raw = value.replace("slot_", "")
        if len(raw) == 4 and raw.isdigit():
            return raw[:2] + ":" + raw[2:]
        return raw

    return value


def is_valid_date(d):
    try:
        selected = datetime.strptime(str(d), "%Y-%m-%d").date()
        return selected >= date.today()
    except Exception:
        return False


def parse_common_date(message):
    msg = message.lower().strip()
    today = date.today()

    if msg == "today":
        return today.strftime("%Y-%m-%d")

    if msg in ["tomorrow", "tmr", "tom"]:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    days = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    for day_name, day_num in days.items():
        if day_name in msg:
            current = today.weekday()
            diff = day_num - current

            if diff <= 0 or "next" in msg:
                diff += 7

            return (today + timedelta(days=diff)).strftime("%Y-%m-%d")

    return ""


def doctor_list_text():
    doctors = Doctor.query.order_by(Doctor.id).all()
    return "\n".join([f"{d.id}. {d.name} - {d.specialization}" for d in doctors])


def get_or_create_conversation(phone):
    convo = Conversation.query.filter_by(phone=str(phone)).first()

    if not convo:
        convo = Conversation(phone=str(phone), step="menu")
        db.session.add(convo)
        db.session.commit()

    return convo


def reset_conversation_booking(convo):
    convo.patient_name = ""
    convo.step = "booking"
    convo.doctor_id = ""
    convo.date = ""
    convo.time_period = ""
    convo.appointment_id = ""


def clear_conversation_state(phone):
    convo = Conversation.query.filter_by(phone=str(phone)).first()

    if convo:
        convo.patient_name = ""
        convo.step = "menu"
        convo.doctor_id = ""
        convo.date = ""
        convo.time_period = ""
        convo.appointment_id = ""
        db.session.commit()


def active_appointments(phone):
    return Appointment.query.filter(
        Appointment.phone == str(phone),
        Appointment.status.in_(["booked", "confirmed"])
    ).order_by(Appointment.id.asc()).all()


def get_doctor_slots(doctor_id, selected_date):
    doctor = Doctor.query.get(int(doctor_id))

    if not doctor:
        return []

    selected_day = datetime.strptime(selected_date, "%Y-%m-%d").strftime("%A")
    working_days = [d.strip() for d in doctor.working_days.split(",")]

    if selected_day not in working_days:
        return []

    start = datetime.strptime(doctor.start_time, "%H:%M")
    end = datetime.strptime(doctor.end_time, "%H:%M")
    current = start
    slots = []

    while current < end:
        t = current.strftime("%H:%M")

        if t < "13:00" or t >= "14:00":
            slots.append(t)

        current += timedelta(minutes=doctor.slot_minutes)

    return slots


def get_available_slots(doctor_id, selected_date):
    all_slots = get_doctor_slots(doctor_id, selected_date)

    if selected_date == date.today().strftime("%Y-%m-%d"):
        now_time = datetime.now().strftime("%H:%M")
        all_slots = [s for s in all_slots if s > now_time]

    booked = Appointment.query.filter(
        Appointment.doctor_id == int(doctor_id),
        Appointment.date == selected_date,
        Appointment.status.in_(["booked", "confirmed"])
    ).all()

    booked_times = [a.time for a in booked]

    return [s for s in all_slots if s not in booked_times]


def filter_slots(slots, period):
    if period == "morning":
        return [s for s in slots if int(s.split(":")[0]) < 13]

    if period == "afternoon":
        return [s for s in slots if int(s.split(":")[0]) >= 14]

    return slots


def send_whatsapp_message(to, msg):
    if not msg:
        return

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "text",
        "text": {"body": msg},
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    r = requests.post(url, headers=headers, json=payload)
    print("SEND RESPONSE:", r.status_code, r.text)


def send_whatsapp_menu(to):
    rows = [
        {"id": "menu_book", "title": "Book Appointment", "description": "Schedule a new consultation"},
        {"id": "menu_doctors", "title": "View Doctors", "description": "Browse our medical team"},
        {"id": "menu_timings", "title": "Clinic Timings", "description": "Check opening hours"},
        {"id": "menu_cancel", "title": "Cancel Appointment", "description": "Cancel your booking"},
        {"id": "menu_reschedule", "title": "Reschedule", "description": "Change appointment date/time"},
    ]

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Clinic Menu"},
            "body": {"text": "Welcome to ABC Clinic 👋\n\nHow can we help you today?"},
            "footer": {"text": "ABC Clinic"},
            "action": {
                "button": "Select Option",
                "sections": [{"title": "Available Actions", "rows": rows}]
            },
        },
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    r = requests.post(url, headers=headers, json=payload)
    print("MENU RESPONSE:", r.status_code, r.text)


def send_whatsapp_doctor_list(to):
    doctors = Doctor.query.order_by(Doctor.id).all()

    rows = [
        {
            "id": f"doctor_{doc.id}",
            "title": doc.name[:24],
            "description": doc.specialization[:72],
        }
        for doc in doctors
    ]

    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Select Doctor"},
            "body": {"text": "Please select a doctor for your appointment:"},
            "footer": {"text": "ABC Clinic"},
            "action": {
                "button": "Choose Doctor",
                "sections": [{"title": "Doctors", "rows": rows}]
            },
        },
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    r = requests.post(url, headers=headers, json=payload)
    print("DOCTOR LIST RESPONSE:", r.status_code, r.text)


def send_whatsapp_period_buttons(to):
    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "Please choose your preferred time period:"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "period_morning", "title": "Morning"}},
                    {"type": "reply", "reply": {"id": "period_afternoon", "title": "Afternoon"}},
                ]
            },
        },
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    r = requests.post(url, headers=headers, json=payload)
    print("PERIOD RESPONSE:", r.status_code, r.text)


def send_whatsapp_cancel_buttons(to, details):
    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": f"Are you sure you want to cancel this appointment?\n\n{details}"
            },
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "cancel_yes", "title": "Yes, cancel"}},
                    {"type": "reply", "reply": {"id": "cancel_no", "title": "No, keep it"}},
                ]
            },
        },
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    r = requests.post(url, headers=headers, json=payload)
    print("CANCEL BUTTONS RESPONSE:", r.status_code, r.text)


def send_whatsapp_slot_list(to, slots):
    rows = []

    for slot in slots[:10]:
        slot_id = slot.replace(":", "")
        rows.append({
            "id": f"slot_{slot_id}",
            "title": to_am_pm(slot),
            "description": "Available appointment slot",
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
                "sections": [{"title": "Available Time Slots", "rows": rows}],
            },
        },
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    r = requests.post(url, headers=headers, json=payload)
    print("SLOT LIST RESPONSE:", r.status_code, r.text)


def fallback_ai(message):
    msg = message.lower().strip()

    result = {
        "intent": "unknown",
        "doctor_id": "",
        "date_iso": "",
        "time_period": "",
        "slot_number": "",
        "patient_name": "",
        "cancel_confirmation": "",
        "doctor_specialization": "",
    }

    parsed_date = parse_common_date(message)

    if parsed_date:
        result["date_iso"] = parsed_date

    if any(x in msg for x in ["tooth", "teeth", "dental", "dentist", "cavity", "gum"]):
        result["intent"] = "book"
        result["doctor_id"] = "2"

    elif any(x in msg for x in ["skin", "rash", "acne", "allergy", "pimple", "dermatologist"]):
        result["intent"] = "book"
        result["doctor_id"] = "1"

    elif any(x in msg for x in ["fever", "cold", "cough", "headache", "body pain", "stomach"]):
        result["intent"] = "book"
        result["doctor_id"] = "3"

    elif any(x in msg for x in ["book", "appointment", "consult", "schedule"]):
        result["intent"] = "book"

    if "cancel" in msg:
        result["intent"] = "cancel"

    if any(x in msg for x in ["reschedule", "postpone", "change appointment", "move appointment"]):
        result["intent"] = "reschedule"

    if any(x in msg for x in ["doctor", "doctors", "specialist"]):
        result["intent"] = "view_doctors"

    if any(x in msg for x in ["timing", "time", "open", "close", "hours"]):
        result["intent"] = "clinic_timings"

    if any(x in msg for x in ["morning", "am"]):
        result["time_period"] = "morning"

    if any(x in msg for x in ["afternoon", "evening", "pm"]):
        result["time_period"] = "afternoon"

    if msg in ["yes", "yes cancel"]:
        result["cancel_confirmation"] = "yes"

    if msg in ["no", "keep", "keep appointment"]:
        result["cancel_confirmation"] = "no"

    return result


def ask_ai(message, step):
    fallback = fallback_ai(message)

    if not GEMINI_API_KEY:
        return fallback

    doctors = Doctor.query.order_by(Doctor.id).all()

    doctor_data = [
        {
            "doctor_id": str(d.id),
            "name": d.name,
            "specialization": d.specialization,
        }
        for d in doctors
    ]

    prompt = f"""
You are an AI receptionist for ABC Clinic.

Today is {date.today().strftime("%Y-%m-%d")}.
Current conversation step is: {step}

Doctors:
{doctor_data}

User message:
"{message}"

Return ONLY valid JSON:
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
"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        }

        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }

        r = requests.post(url, headers=headers, json=payload, timeout=20)
        data = r.json()

        print("GEMINI STATUS:", r.status_code)
        print("GEMINI RESPONSE:", data)

        if "candidates" not in data:
            print("GEMINI FAILED, USING FALLBACK AI")
            return fallback

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()

        ai = json.loads(text)

        for key in fallback:
            if not ai.get(key) and fallback.get(key):
                ai[key] = fallback[key]

        return ai

    except Exception as e:
        print("GEMINI ERROR:", e)
        return fallback


def ask_next_missing_info(phone, convo):
    if not convo.patient_name:
        convo.step = "ask_name"
        db.session.commit()
        return action("text", "Please enter your full name.")

    if not convo.doctor_id:
        convo.step = "ask_doctor"
        db.session.commit()
        return action("doctors")

    doctor = Doctor.query.get(int(convo.doctor_id))

    if not doctor:
        convo.doctor_id = ""
        convo.step = "ask_doctor"
        db.session.commit()
        return action("doctors")

    if not convo.date or not is_valid_date(convo.date):
        convo.step = "ask_date"
        db.session.commit()

        return action(
            "text",
            f"Perfect. {doctor.name} ({doctor.specialization}) is selected.\n\n"
            "Please tell me the appointment date.\n"
            "Example: tomorrow / next Monday / 2026-06-10"
        )

    if not convo.time_period:
        convo.step = "ask_period"
        db.session.commit()
        return action("period")

    available = get_available_slots(convo.doctor_id, convo.date)
    filtered = filter_slots(available, convo.time_period)

    if not filtered:
        convo.time_period = ""
        convo.step = "ask_period"
        db.session.commit()

        return action(
            "text",
            "No slots available for that time period. Please choose another time period."
        )

    convo.step = "choose_slot"
    db.session.commit()

    return action("slots", filtered)


def book_or_reschedule_slot(phone, convo, selected_slot):
    selected_slot = normalize_time_id(selected_slot)

    doctor = Doctor.query.get(int(convo.doctor_id))

    if not doctor:
        convo.step = "ask_doctor"
        convo.doctor_id = ""
        db.session.commit()
        return action("doctors")

    available = get_available_slots(convo.doctor_id, convo.date)
    filtered = filter_slots(available, convo.time_period)

    if selected_slot.isdigit():
        index = int(selected_slot) - 1
        if index < 0 or index >= len(filtered):
            return action("text", "Please choose a valid slot.")
        selected_time = filtered[index]
    else:
        selected_time = selected_slot

    if selected_time not in filtered:
        return action("text", "That slot is no longer available. Please choose again.")

    if convo.appointment_id:
        appt = Appointment.query.get(int(convo.appointment_id))

        if not appt:
            return action("text", "Appointment not found.")

        appt.doctor_id = int(convo.doctor_id)
        appt.doctor_name = doctor.name
        appt.date = convo.date
        appt.time = selected_time
        appt.status = "booked"

        start_msg = "Appointment rescheduled successfully ✅"

    else:
        appt = Appointment(
            patient_name=convo.patient_name,
            phone=str(phone),
            doctor_id=int(convo.doctor_id),
            doctor_name=doctor.name,
            date=convo.date,
            time=selected_time,
            status="booked",
        )

        db.session.add(appt)
        db.session.flush()

        start_msg = "Appointment successfully booked ✅"

    patient = Patient.query.filter_by(phone=str(phone)).first()

    if not patient:
        patient = Patient(name=convo.patient_name, phone=str(phone))
        db.session.add(patient)

    convo.step = "completed"
    convo.appointment_id = str(appt.id)

    db.session.commit()

    return action(
        "text",
        f"{start_msg}\n\n"
        f"Patient: {convo.patient_name}\n"
        f"Doctor: {doctor.name}\n"
        f"Date: {convo.date}\n"
        f"Time: {to_am_pm(selected_time)}\n\n"
        "ABC Clinic will confirm your appointment soon.\n\n"
        "You can type 'cancel appointment' or 'reschedule appointment' anytime."
    )


def process_chat_message(phone, message):
    message = str(message).strip()
    lower = message.lower().strip()

    convo = get_or_create_conversation(phone)
    step = convo.step

    if lower in ["hi", "hello", "hey", "menu", "start"]:
        convo.step = "menu"
        db.session.commit()
        return action("menu")

    if lower in ["menu_book", "1"]:
        reset_conversation_booking(convo)
        db.session.commit()
        return ask_next_missing_info(phone, convo)

    if lower in ["menu_doctors", "2"]:
        convo.step = "menu"
        db.session.commit()
        return action("text", "Our doctors are:\n\n" + doctor_list_text())

    if lower in ["menu_timings", "3"]:
        convo.step = "menu"
        db.session.commit()
        return action(
            "text",
            "ABC Clinic Timings:\n\n"
            "Monday to Saturday\n"
            "9:00 AM to 6:00 PM\n\n"
            "Lunch: 1:00 PM to 2:00 PM\n"
            "Sunday closed."
        )

    if lower in ["menu_cancel", "4", "cancel appointment"]:
        active = active_appointments(phone)

        if not active:
            convo.step = "menu"
            db.session.commit()
            return action("text", "You do not have any active appointment to cancel.")

        appt = active[-1]
        convo.step = "confirm_cancel"
        convo.appointment_id = str(appt.id)
        db.session.commit()

        details = f"Doctor: {appt.doctor_name}\nDate: {appt.date}\nTime: {to_am_pm(appt.time)}"
        return action("cancel", details)

    if lower in ["menu_reschedule", "5", "reschedule appointment"]:
        active = active_appointments(phone)

        if not active:
            return action("text", "You do not have any active appointment to reschedule.")

        appt = active[-1]

        convo.step = "booking"
        convo.appointment_id = str(appt.id)
        convo.patient_name = appt.patient_name
        convo.doctor_id = str(appt.doctor_id)
        convo.date = ""
        convo.time_period = ""
        db.session.commit()

        return action(
            "text",
            "Okay, let's reschedule your appointment.\n\n"
            f"Current appointment:\n"
            f"Doctor: {appt.doctor_name}\n"
            f"Date: {appt.date}\n"
            f"Time: {to_am_pm(appt.time)}\n\n"
            "Please tell me the new date."
        )

    if step == "confirm_cancel":
        if lower in ["cancel_yes", "yes", "yes cancel", "1"]:
            appt = Appointment.query.get(int(convo.appointment_id))

            if not appt:
                clear_conversation_state(phone)
                return action("text", "Appointment not found.")

            appt.status = "cancelled"
            db.session.commit()
            clear_conversation_state(phone)

            return action(
                "text",
                "Your appointment has been cancelled ❌\n\n"
                f"Doctor: {appt.doctor_name}\n"
                f"Date: {appt.date}\n"
                f"Time: {to_am_pm(appt.time)}"
            )

        if lower in ["cancel_no", "no", "2"]:
            convo.step = "completed"
            db.session.commit()
            return action("text", "Okay, your appointment remains booked ✅")

        return action("text", "Please choose Yes or No.")

    if step == "ask_name":
        convo.patient_name = message
        convo.step = "booking"
        db.session.commit()
        return ask_next_missing_info(phone, convo)

    if step == "ask_doctor":
        if lower.startswith("doctor_"):
            doctor_id = lower.replace("doctor_", "")

        elif lower.isdigit():
            doctor_id = lower

        else:
            doctor = Doctor.query.filter(
                db.func.lower(Doctor.specialization).contains(lower)
            ).first()
            doctor_id = str(doctor.id) if doctor else ""

        if not doctor_id or not Doctor.query.get(int(doctor_id)):
            return action("doctors")

        convo.doctor_id = doctor_id
        convo.step = "booking"
        db.session.commit()

        return ask_next_missing_info(phone, convo)

    if step == "ask_date":
        parsed = parse_common_date(message)

        if not parsed and is_valid_date(message):
            parsed = message

        if not parsed:
            ai = ask_ai(message, step)
            parsed = ai.get("date_iso", "")

        if not parsed or not is_valid_date(parsed):
            return action(
                "text",
                "Please enter a valid future date.\n\nExamples:\n2026-06-10\ntomorrow\nnext Monday"
            )

        convo.date = parsed
        convo.step = "booking"
        db.session.commit()

        return ask_next_missing_info(phone, convo)

    if step == "ask_period":
        if lower in ["period_morning", "morning", "1"]:
            convo.time_period = "morning"

        elif lower in ["period_afternoon", "afternoon", "2"]:
            convo.time_period = "afternoon"

        else:
            return action("period")

        convo.step = "booking"
        db.session.commit()

        return ask_next_missing_info(phone, convo)

    if step == "choose_slot":
        return book_or_reschedule_slot(phone, convo, message)

    if step == "completed":
        if "book" in lower or "appointment" in lower:
            reset_conversation_booking(convo)
            db.session.commit()
            return ask_next_missing_info(phone, convo)

        return action(
            "text",
            "You already have a booking.\n\n"
            "You can type:\n"
            "• cancel appointment\n"
            "• reschedule appointment\n"
            "• menu"
        )

    ai = ask_ai(message, step)

    intent = ai.get("intent", "unknown")
    doctor_id = str(ai.get("doctor_id", "")).strip()
    date_iso = str(ai.get("date_iso", "")).strip()
    period = str(ai.get("time_period", "")).strip()

    if intent == "book":
        reset_conversation_booking(convo)

        if doctor_id:
            convo.doctor_id = doctor_id

        if date_iso and is_valid_date(date_iso):
            convo.date = date_iso

        if period in ["morning", "afternoon"]:
            convo.time_period = period

        db.session.commit()
        return ask_next_missing_info(phone, convo)

    if intent == "view_doctors":
        return action("doctors")

    if intent == "clinic_timings":
        return action(
            "text",
            "ABC Clinic Timings:\n\n"
            "Monday to Saturday\n"
            "9:00 AM to 6:00 PM\n\n"
            "Lunch: 1:00 PM to 2:00 PM\n"
            "Sunday closed."
        )

    return action("menu")


def send_action(phone, result):
    kind = result.get("kind")
    text = result.get("text", "")

    if kind == "menu":
        send_whatsapp_menu(phone)

    elif kind == "doctors":
        send_whatsapp_doctor_list(phone)

    elif kind == "period":
        send_whatsapp_period_buttons(phone)

    elif kind == "cancel":
        send_whatsapp_cancel_buttons(phone, text)

    elif kind == "slots":
        send_whatsapp_slot_list(phone, text)

    else:
        send_whatsapp_message(phone, text)


def appointment_to_dict(a):
    return {
        "appointment_id": a.id,
        "patient_name": a.patient_name,
        "phone": a.phone,
        "doctor_id": a.doctor_id,
        "doctor_name": a.doctor_name,
        "date": a.date,
        "time": a.time,
        "status": a.status,
        "created_at": a.created_at,
        "reminder_sent": a.reminder_sent,
    }


def doctor_to_dict(d):
    return {
        "doctor_id": d.id,
        "name": d.name,
        "specialization": d.specialization,
        "working_days": d.working_days,
        "start_time": d.start_time,
        "end_time": d.end_time,
        "slot_minutes": d.slot_minutes,
    }


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
            message_id = messages[0].get("id")

            if message_id:
                already_done = ProcessedMessage.query.filter_by(message_id=message_id).first()

                if already_done:
                    print("DUPLICATE MESSAGE IGNORED:", message_id)
                    return "EVENT_RECEIVED", 200

                db.session.add(ProcessedMessage(message_id=message_id))
                db.session.commit()

            phone = messages[0]["from"]
            text = ""

            if "text" in messages[0]:
                text = messages[0]["text"]["body"]

            elif "interactive" in messages[0]:
                interactive = messages[0]["interactive"]

                if interactive["type"] == "list_reply":
                    text = interactive["list_reply"]["id"]

                elif interactive["type"] == "button_reply":
                    text = interactive["button_reply"]["id"]

            if text:
                result = process_chat_message(phone, text)
                send_action(phone, result)

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return "EVENT_RECEIVED", 200


@app.route("/doctors", methods=["GET"])
def get_doctors():
    doctors = Doctor.query.order_by(Doctor.id).all()
    return jsonify([doctor_to_dict(d) for d in doctors])


@app.route("/appointments", methods=["GET"])
def get_appointments():
    appointments = Appointment.query.order_by(Appointment.id.desc()).all()
    return jsonify([appointment_to_dict(a) for a in appointments])


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json

    result = process_chat_message(
        str(data.get("phone", "")),
        str(data.get("message", ""))
    )

    return jsonify(result)


@app.route("/add-doctor", methods=["POST"])
def add_doctor():
    data = request.json

    name = str(data.get("name", "")).strip()
    specialization = str(data.get("specialization", "")).strip()

    if not name or not specialization:
        return jsonify({"success": False, "message": "Doctor name and specialization required"}), 400

    doctor = Doctor(name=name, specialization=specialization)
    db.session.add(doctor)
    db.session.commit()

    return jsonify({"success": True, "doctor": doctor_to_dict(doctor)})


@app.route("/delete-doctor/<int:doctor_id>", methods=["POST"])
def delete_doctor(doctor_id):
    active = Appointment.query.filter(
        Appointment.doctor_id == doctor_id,
        Appointment.status.in_(["booked", "confirmed"])
    ).first()

    if active:
        return jsonify({"success": False, "message": "Cannot delete doctor with active appointments"}), 409

    doctor = Doctor.query.get(doctor_id)

    if not doctor:
        return jsonify({"success": False, "message": "Doctor not found"}), 404

    db.session.delete(doctor)
    db.session.commit()

    return jsonify({"success": True})


@app.route("/book", methods=["POST"])
def manual_book():
    data = request.json

    patient_name = str(data.get("patient_name", "")).strip()
    phone = str(data.get("phone", "")).strip()
    doctor_id = str(data.get("doctor_id", "")).strip()
    selected_date = str(data.get("date", "")).strip()
    selected_time = str(data.get("time", "")).strip()

    if not patient_name or not phone or not doctor_id or not selected_date or not selected_time:
        return jsonify({"success": False, "message": "All fields required"}), 400

    if selected_time not in get_available_slots(doctor_id, selected_date):
        return jsonify({"success": False, "message": "Slot not available"}), 409

    doctor = Doctor.query.get(int(doctor_id))

    if not doctor:
        return jsonify({"success": False, "message": "Doctor not found"}), 404

    appt = Appointment(
        patient_name=patient_name,
        phone=phone,
        doctor_id=int(doctor_id),
        doctor_name=doctor.name,
        date=selected_date,
        time=selected_time,
        status="booked",
    )

    db.session.add(appt)
    db.session.commit()

    return jsonify({"success": True, "appointment": appointment_to_dict(appt)})


@app.route("/confirm/<int:appointment_id>", methods=["POST"])
def confirm_appointment(appointment_id):
    appt = Appointment.query.get(appointment_id)

    if not appt:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    appt.status = "confirmed"
    db.session.commit()

    msg = (
        "Your appointment has been confirmed ✅\n\n"
        f"Patient: {appt.patient_name}\n"
        f"Doctor: {appt.doctor_name}\n"
        f"Date: {appt.date}\n"
        f"Time: {to_am_pm(appt.time)}"
    )

    send_whatsapp_message(appt.phone, msg)

    return jsonify({"success": True, "whatsapp_message": msg})


@app.route("/cancel/<int:appointment_id>", methods=["POST"])
def cancel_appointment(appointment_id):
    appt = Appointment.query.get(appointment_id)

    if not appt:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    appt.status = "cancelled"
    db.session.commit()
    clear_conversation_state(appt.phone)

    msg = (
        "Your appointment has been cancelled ❌\n\n"
        f"Doctor: {appt.doctor_name}\n"
        f"Date: {appt.date}\n"
        f"Time: {to_am_pm(appt.time)}"
    )

    send_whatsapp_message(appt.phone, msg)

    return jsonify({"success": True, "whatsapp_message": msg})


@app.route("/complete/<int:appointment_id>", methods=["POST"])
def complete_appointment(appointment_id):
    appt = Appointment.query.get(appointment_id)

    if not appt:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    appt.status = "completed"
    db.session.commit()
    clear_conversation_state(appt.phone)

    msg = f"Thank you for visiting ABC Clinic, {appt.patient_name} 😊"
    send_whatsapp_message(appt.phone, msg)

    return jsonify({"success": True, "whatsapp_message": msg})


@app.route("/reminder/<int:appointment_id>", methods=["POST"])
def reminder(appointment_id):
    appt = Appointment.query.get(appointment_id)

    if not appt:
        return jsonify({"success": False, "message": "Appointment not found"}), 404

    msg = (
        "Reminder from ABC Clinic ⏰\n\n"
        f"Hi {appt.patient_name},\n"
        f"Your appointment with {appt.doctor_name} is on {appt.date} at {to_am_pm(appt.time)}."
    )

    send_whatsapp_message(appt.phone, msg)

    return jsonify({"success": True, "whatsapp_message": msg})


@app.route("/reset-chat/<phone>", methods=["POST", "GET"])
def reset_chat(phone):
    clear_conversation_state(phone)
    return jsonify({"success": True, "message": "Chat reset successfully"})


if __name__ == "__main__":
    app.run(debug=True, port=5055)