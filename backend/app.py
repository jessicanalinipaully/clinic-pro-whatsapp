import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import os
from datetime import datetime
import random
import re

app = Flask(__name__)
CORS(app)

EXCEL_FILE = "database.xlsx"
VERIFY_TOKEN = "clinic_verify_123"
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")


def create_excel_if_missing():
    if not os.path.exists(EXCEL_FILE):
        patients = pd.DataFrame({
            "patient_id": pd.Series(dtype="object"),
            "name": pd.Series(dtype="object"),
            "phone": pd.Series(dtype="object")
        })

        doctors = pd.DataFrame([
            {"doctor_id": "1", "name": "Dr Priya", "specialization": "Dermatologist"},
            {"doctor_id": "2", "name": "Dr Kumar", "specialization": "Dentist"},
            {"doctor_id": "3", "name": "Dr Mehta", "specialization": "General Physician"},
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
            "created_at": pd.Series(dtype="object")
        })

        conversations = pd.DataFrame({
            "phone": pd.Series(dtype="object"),
            "patient_name": pd.Series(dtype="object"),
            "step": pd.Series(dtype="object"),
            "otp": pd.Series(dtype="object"),
            "verified": pd.Series(dtype="object"),
            "doctor_id": pd.Series(dtype="object"),
            "date": pd.Series(dtype="object"),
            "time": pd.Series(dtype="object")
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


def write_all_sheets(patients, doctors, appointments, conversations):
    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
        patients.astype(object).to_excel(writer, sheet_name="patients", index=False)
        doctors.astype(object).to_excel(writer, sheet_name="doctors", index=False)
        appointments.astype(object).to_excel(writer, sheet_name="appointments", index=False)
        conversations.astype(object).to_excel(writer, sheet_name="conversations", index=False)


def load_all():
    return (
        read_sheet("patients"),
        read_sheet("doctors"),
        read_sheet("appointments"),
        read_sheet("conversations")
    )


def is_valid_date(date_text):
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def is_valid_time(time_text):
    pattern = r"^([01]\d|2[0-3]):[0-5]\d$"
    return re.match(pattern, time_text) is not None


def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": message
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload
    )

    print("SEND RESPONSE:", response.status_code)
    print(response.text)


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
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        messages = value.get("messages", [])

        if messages:
            phone = messages[0]["from"]
            text = messages[0]["text"]["body"]

            print("MESSAGE:", text)

            reply = (
                "Welcome to ABC Clinic 👋\n\n"
                "1. Book Appointment\n"
                "2. View Doctors\n"
                "3. Clinic Timings"
            )

            send_whatsapp_message(phone, reply)

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return "EVENT_RECEIVED", 200


@app.route("/doctors", methods=["GET"])
def get_doctors():
    doctors = read_sheet("doctors")
    return jsonify(doctors.to_dict(orient="records"))


@app.route("/appointments", methods=["GET"])
def get_appointments():
    appointments = read_sheet("appointments")
    return jsonify(appointments.to_dict(orient="records"))


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    phone = str(data.get("phone", "")).strip()
    message = str(data.get("message", "")).strip()

    reply = "WhatsApp webhook is connected. Bot message received."

    return jsonify({"reply": reply})


@app.route("/add-doctor", methods=["POST"])
def add_doctor():
    data = request.json

    name = str(data.get("name", "")).strip()
    specialization = str(data.get("specialization", "")).strip()

    if not name or not specialization:
        return jsonify({
            "success": False,
            "message": "Doctor name and specialization are required"
        }), 400

    patients, doctors, appointments, conversations = load_all()

    existing_ids = doctors["doctor_id"].astype(str).tolist() if not doctors.empty else []
    next_id = 1

    while str(next_id) in existing_ids:
        next_id += 1

    new_doctor = {
        "doctor_id": str(next_id),
        "name": name,
        "specialization": specialization
    }

    doctors = pd.concat(
        [doctors, pd.DataFrame([new_doctor], dtype=object)],
        ignore_index=True
    )

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({
        "success": True,
        "message": "Doctor added successfully",
        "doctor": new_doctor
    })


@app.route("/delete-doctor/<doctor_id>", methods=["POST"])
def delete_doctor(doctor_id):
    patients, doctors, appointments, conversations = load_all()

    active_appointments = appointments[
        (appointments["doctor_id"].astype(str) == str(doctor_id)) &
        (appointments["status"].astype(str).isin(["booked", "confirmed"]))
    ]

    if not active_appointments.empty:
        return jsonify({
            "success": False,
            "message": "Cannot delete doctor with active appointments"
        }), 409

    doctors = doctors[doctors["doctor_id"].astype(str) != str(doctor_id)]

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({
        "success": True,
        "message": "Doctor deleted successfully"
    })


@app.route("/book", methods=["POST"])
def book_appointment():
    data = request.json

    patient_name = str(data.get("patient_name", "")).strip()
    phone = str(data.get("phone", "")).strip()
    doctor_id = str(data.get("doctor_id", "")).strip()
    date = str(data.get("date", "")).strip()
    time = str(data.get("time", "")).strip()

    if not patient_name or not phone or not doctor_id or not date or not time:
        return jsonify({"success": False, "message": "All fields are required"}), 400

    if not is_valid_date(date):
        return jsonify({"success": False, "message": "Invalid date format. Use YYYY-MM-DD"}), 400

    if not is_valid_time(time):
        return jsonify({"success": False, "message": "Invalid time format. Use HH:MM"}), 400

    patients, doctors, appointments, conversations = load_all()

    doctor_row = doctors[doctors["doctor_id"].astype(str) == doctor_id]

    if doctor_row.empty:
        return jsonify({"success": False, "message": "Doctor not found"}), 404

    doctor_name = doctor_row.iloc[0]["name"]

    clash = appointments[
        (appointments["doctor_id"].astype(str) == doctor_id) &
        (appointments["date"].astype(str) == date) &
        (appointments["time"].astype(str) == time) &
        (appointments["status"].astype(str).isin(["booked", "confirmed"]))
    ]

    if not clash.empty:
        return jsonify({
            "success": False,
            "message": "Doctor is not free at this time"
        }), 409

    if phone not in patients["phone"].astype(str).values:
        new_patient = {
            "patient_id": str(len(patients) + 1),
            "name": patient_name,
            "phone": phone
        }
        patients = pd.concat([patients, pd.DataFrame([new_patient], dtype=object)], ignore_index=True)

    new_appointment = {
        "appointment_id": str(len(appointments) + 1),
        "patient_name": patient_name,
        "phone": phone,
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "date": date,
        "time": time,
        "status": "booked",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    appointments = pd.concat([appointments, pd.DataFrame([new_appointment], dtype=object)], ignore_index=True)

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({
        "success": True,
        "message": "Appointment booked successfully",
        "appointment": new_appointment
    })


@app.route("/confirm/<int:appointment_id>", methods=["POST"])
def confirm_appointment(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    appointments.loc[
        appointments["appointment_id"].astype(str) == str(appointment_id),
        "status"
    ] = "confirmed"

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({
        "success": True,
        "message": "Appointment confirmed successfully",
        "whatsapp_message": "Your appointment has been confirmed by ABC Clinic."
    })


@app.route("/complete/<int:appointment_id>", methods=["POST"])
def complete_appointment(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    appointments.loc[
        appointments["appointment_id"].astype(str) == str(appointment_id),
        "status"
    ] = "completed"

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({
        "success": True,
        "message": "Appointment marked as completed",
        "whatsapp_message": "Thank you for visiting ABC Clinic. We hope your consultation went well."
    })


@app.route("/cancel/<int:appointment_id>", methods=["POST"])
def cancel_appointment(appointment_id):
    patients, doctors, appointments, conversations = load_all()

    appointments.loc[
        appointments["appointment_id"].astype(str) == str(appointment_id),
        "status"
    ] = "cancelled"

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({
        "success": True,
        "message": "Appointment cancelled successfully"
    })


@app.route("/reset-chat/<phone>", methods=["POST"])
def reset_chat(phone):
    patients, doctors, appointments, conversations = load_all()

    conversations = conversations[
        conversations["phone"].astype(str) != str(phone)
    ]

    write_all_sheets(patients, doctors, appointments, conversations)

    return jsonify({
        "success": True,
        "message": "Chat reset successfully"
    })


if __name__ == "__main__":
    create_excel_if_missing()
    app.run(debug=True, port=5055)