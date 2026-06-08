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


def to_am_pm(t):
    return datetime.strptime(str(t), "%H:%M").strftime("%I:%M %p")


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


def send_whatsapp_slot_list(to, slots):
    rows = []

    for i, slot in enumerate(slots[:10], start=1):
        rows.append({
            "id": f"slot_{i}",
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
                "sections": [
                    {
                        "title": "Available Time Slots",
                        "rows": rows,
                    }
                ],
            },
        },
    }

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    r = requests.post(url, headers=headers, json=payload)
    print("LIST RESPONSE:", r.status_code, r.text)


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

    if msg.isdigit():
        result["slot_number"] = msg

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

    if any(x in msg for x in ["morning", "am"]):
        result["time_period"] = "morning"

    if any(x in msg for x in ["afternoon", "evening", "pm"]):
        result["time_period"] = "afternoon"

    if msg in ["yes", "yes cancel", "1"]:
        result["cancel_confirmation"] = "yes"

    if msg in ["no", "keep", "keep appointment", "2"]:
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

Available doctors:
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

Rules:
- Skin/acne/rash/allergy/skin specialist/dermatology -> doctor_id "1".
- Tooth pain/dental/dentist/cavity -> doctor_id "2".
- Fever/cough/cold/headache/body pain/general sickness -> doctor_id "3".
- Convert natural dates like tomorrow, next Monday, next week Tuesday, 3rd of next month into YYYY-MM-DD.
- If user says morning/AM, time_period morning.
- If user says afternoon/evening/PM, time_period afternoon.
- If user gives name during ask_name, put patient_name.
- If user selects slot number, put slot_number.
- If user confirms cancellation, put cancel_confirmation yes or no.
"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        }

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ]
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
        print("AI RESPONSE:", ai)

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
        return "Please enter your full name."

    if not convo.doctor_id:
        convo.step = "ask_doctor"
        db.session.commit()
        return "Choose doctor:\n\n" + doctor_list_text()

    doctor = Doctor.query.get(int(convo.doctor_id))

    if not convo.date or not is_valid_date(convo.date):
        convo.step = "ask_date"
        db.session.commit()
        return (
            f"Perfect. {doctor.name} ({doctor.specialization}) is selected.\n\n"
            "Please tell me the appointment date.\n"
            "Example: tomorrow / next Monday / 2026-06-10"
        )

    if not convo.time_period:
        convo.step = "ask_period"
        db.session.commit()
        return "Which time do you prefer?\n\n1. Morning\n2. Afternoon"

    available = get_available_slots(convo.doctor_id, convo.date)
    filtered = filter_slots(available, convo.time_period)

    if not filtered:
        convo.time_period = ""
        convo.step = "ask_period"
        db.session.commit()
        return "No slots available for that time period. Please choose:\n\n1. Morning\n2. Afternoon"

    convo.step = "choose_slot"
    db.session.commit()

    send_whatsapp_slot_list(phone, filtered)
    return "Please tap Choose Time and select your preferred slot."


def book_or_reschedule_slot(phone, convo, slot_number):
    doctor = Doctor.query.get(int(convo.doctor_id))

    available = get_available_slots(convo.doctor_id, convo.date)
    filtered = filter_slots(available, convo.time_period)

    try:
        choice = int(slot_number)
    except Exception:
        return "Please select a slot from the list."

    if choice < 1 or choice > len(filtered):
        return "Please choose a valid slot."

    selected_time = filtered[choice - 1]

    if convo.appointment_id:
        appt = Appointment.query.get(int(convo.appointment_id))

        if not appt:
            return "Appointment not found."

        appt.date = convo.date
        appt.time = selected_time
        appt.status = "booked"
        start_msg = "Appointment rescheduled successfully ✅"

    else:
        existing = Appointment.query.filter_by(
            doctor_id=int(convo.doctor_id),
            date=convo.date,
            time=selected_time,
            status="booked",
        ).first()

        if existing:
            convo.step = "ask_period"
            db.session.commit()
            return "Sorry, that slot just got booked. Please choose another time period."

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

    return (
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

    if lower in ["hi", "hello", "menu", "start"]:
        convo.step = "menu"
        db.session.commit()
        return show_menu()
    # PERMANENT FIX:
    # If user is selecting a time slot, do NOT call Gemini.
    # Otherwise slot number like 3 can be misunderstood as doctor_id 3.
    if step == "choose_slot":
        return book_or_reschedule_slot(phone, convo, message)
    ai = ask_ai(message, step)

    intent = ai.get("intent", "unknown")
    doctor_id = str(ai.get("doctor_id", "")).strip()
    date_iso = str(ai.get("date_iso", "")).strip()
    period = str(ai.get("time_period", "")).strip()
    slot_number = str(ai.get("slot_number", "")).strip()
    patient_name = str(ai.get("patient_name", "")).strip()
    cancel_confirmation = str(ai.get("cancel_confirmation", "")).strip()
    doctor_specialization = str(ai.get("doctor_specialization", "")).strip()

    if step == "confirm_cancel":
        if cancel_confirmation == "yes" or message == "1":
            appt = Appointment.query.get(int(convo.appointment_id))

            if not appt:
                convo.step = "menu"
                convo.appointment_id = ""
                db.session.commit()
                return "Appointment not found."

            appt.status = "cancelled"
            db.session.commit()
            clear_conversation_state(phone)

            return (
                "Your appointment has been cancelled ❌\n\n"
                f"Doctor: {appt.doctor_name}\n"
                f"Date: {appt.date}\n"
                f"Time: {to_am_pm(appt.time)}"
            )

        if cancel_confirmation == "no" or message == "2":
            convo.step = "completed"
            db.session.commit()
            return "Okay, your appointment remains booked ✅"

        return "Please confirm:\n\n1. Yes, cancel appointment\n2. No, keep appointment"

    if intent == "cancel" or (step == "menu" and message == "4"):
        active = active_appointments(phone)

        if not active:
            convo.step = "menu"
            db.session.commit()
            return "You do not have any active appointment to cancel."

        appt = active[-1]
        convo.step = "confirm_cancel"
        convo.appointment_id = str(appt.id)
        db.session.commit()

        return (
            "Are you sure you want to cancel this appointment?\n\n"
            f"Doctor: {appt.doctor_name}\n"
            f"Date: {appt.date}\n"
            f"Time: {to_am_pm(appt.time)}\n\n"
            "Reply:\n1. Yes, cancel appointment\n2. No, keep appointment"
        )

    if intent == "reschedule" or (step == "menu" and message == "5"):
        active = active_appointments(phone)

        if not active:
            return "You do not have any active appointment to reschedule."

        appt = active[-1]

        convo.step = "booking"
        convo.appointment_id = str(appt.id)
        convo.patient_name = appt.patient_name
        convo.doctor_id = str(appt.doctor_id)
        convo.date = ""
        convo.time_period = ""
        db.session.commit()

        return (
            "Okay, let's reschedule your appointment.\n\n"
            f"Current appointment:\n"
            f"Doctor: {appt.doctor_name}\n"
            f"Date: {appt.date}\n"
            f"Time: {to_am_pm(appt.time)}\n\n"
            "Please tell me the new date."
        )

    if step == "menu" and (intent == "clinic_timings" or message == "3"):
        return (
            "ABC Clinic Timings:\n\n"
            "Monday to Saturday\n"
            "9:00 AM to 6:00 PM\n\n"
            "Lunch: 1:00 PM to 2:00 PM\n"
            "Sunday closed."
        )

    if step == "menu" and (intent == "view_doctors" or message == "2"):
        return "Our doctors are:\n\n" + doctor_list_text()

    if intent == "doctor_query" and not doctor_id:
        return (
            f"I'm sorry, we do not currently have a {doctor_specialization or 'doctor for that specialty'} available.\n\n"
            "Available doctors are:\n\n"
            + doctor_list_text()
        )

    booking_steps = ["booking", "ask_name", "ask_doctor", "ask_date", "ask_period", "choose_slot"]

    if intent == "book" or step in booking_steps or (step == "menu" and message == "1"):
        if step == "menu" and message == "1":
            reset_conversation_booking(convo)

        else:
            convo.step = "booking"

            if patient_name and step != "ask_name":
                convo.patient_name = patient_name

            if doctor_id and step not in ["ask_name", "choose_slot"]:
                convo.doctor_id = doctor_id

            if date_iso and is_valid_date(date_iso):
                convo.date = date_iso

            if period in ["morning", "afternoon"]:
                convo.time_period = period

        if step == "ask_name":
            convo.patient_name = message

        if step == "ask_doctor":
            if message in ["1", "2", "3"]:
                convo.doctor_id = message
            elif doctor_id:
                convo.doctor_id = doctor_id

        if step == "ask_date":
            if date_iso and is_valid_date(date_iso):
                convo.date = date_iso
            elif is_valid_date(message):
                convo.date = message

        if step == "ask_period":
            if message == "1" or "morning" in lower:
                convo.time_period = "morning"
            elif message == "2" or "afternoon" in lower:
                convo.time_period = "afternoon"

        db.session.commit()

        if step == "choose_slot":
            selected_slot = slot_number if slot_number else message
            return book_or_reschedule_slot(phone, convo, selected_slot)

        return ask_next_missing_info(phone, convo)

    if step == "completed":
        if "book" in lower or "appointment" in lower or lower in ["1", "new booking"]:
            reset_conversation_booking(convo)
            db.session.commit()
            return ask_next_missing_info(phone, convo)

        return (
            "You already have a booking.\n\n"
            "You can type:\n"
            "• book another appointment\n"
            "• cancel appointment\n"
            "• reschedule appointment\n"
            "• menu"
        )

    return show_menu()


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
                already_done = ProcessedMessage.query.filter_by(
                    message_id=message_id
                ).first()

                if already_done:
                    print("DUPLICATE MESSAGE IGNORED:", message_id)
                    return "EVENT_RECEIVED", 200

                saved_msg = ProcessedMessage(message_id=message_id)
                db.session.add(saved_msg)
                db.session.commit()

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
    doctors = Doctor.query.order_by(Doctor.id).all()
    return jsonify([doctor_to_dict(d) for d in doctors])


@app.route("/appointments", methods=["GET"])
def get_appointments():
    appointments = Appointment.query.order_by(Appointment.id.desc()).all()
    return jsonify([appointment_to_dict(a) for a in appointments])


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    reply = process_chat_message(
        str(data.get("phone", "")),
        str(data.get("message", ""))
    )
    return jsonify({"reply": reply})


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
    convo = Conversation.query.filter_by(phone=str(phone)).first()

    if convo:
        db.session.delete(convo)
        db.session.commit()

    return jsonify({"success": True, "message": "Chat reset successfully"})


if __name__ == "__main__":
    app.run(debug=True, port=5055)