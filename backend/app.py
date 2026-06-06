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


def process_chat_message(phone, message):
    patients, doctors, appointments, conversations = load_all()

    if conversations.empty:
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

    existing = conversations[conversations["phone"].astype(str) == phone]

    if existing.empty:
        otp = str(random.randint(1000, 9999))
        new_conversation = {
            "phone": phone,
            "patient_name": "",
            "step": "ask_name",
            "otp": otp,
            "verified": "False",
            "doctor_id": "",
            "date": "",
            "time": ""
        }

        conversations = pd.concat(
            [conversations, pd.DataFrame([new_conversation], dtype=object)],
            ignore_index=True
        )

        write_all_sheets(patients, doctors, appointments, conversations)
        return "Welcome to ABC Clinic.\nPlease enter your name."

    index = existing.index[0]
    step = str(conversations.loc[index, "step"])

    if step == "ask_name":
        conversations.loc[index, "patient_name"] = message
        conversations.loc[index, "step"] = "ask_otp"
        otp = str(conversations.loc[index, "otp"])

        write_all_sheets(patients, doctors, appointments, conversations)
        return f"Your OTP is {otp}.\nPlease enter OTP to verify your number."

    if step == "ask_otp":
        correct_otp = str(conversations.loc[index, "otp"])

        if message == correct_otp:
            conversations.loc[index, "verified"] = "True"
            conversations.loc[index, "step"] = "ask_doctor"

            doctor_lines = []
            for _, doctor in doctors.iterrows():
                doctor_lines.append(
                    f"{doctor['doctor_id']}. {doctor['name']} - {doctor['specialization']}"
                )

            doctor_text = "\n".join(doctor_lines)
            write_all_sheets(patients, doctors, appointments, conversations)

            return f"Number verified successfully.\nChoose doctor:\n{doctor_text}"

        return "Invalid OTP. Please try again."

    if step == "ask_doctor":
        valid_doctor_ids = doctors["doctor_id"].astype(str).tolist()

        if message not in valid_doctor_ids:
            return "Please choose a valid doctor number."

        conversations.loc[index, "doctor_id"] = message
        conversations.loc[index, "step"] = "ask_date"

        write_all_sheets(patients, doctors, appointments, conversations)

        return "Enter appointment date in this format:\nYYYY-MM-DD\nExample: 2026-06-10"

    if step == "ask_date":
        if not is_valid_date(message):
            return "Invalid date format.\nPlease enter date like this:\nYYYY-MM-DD\nExample: 2026-06-10"

        conversations.loc[index, "date"] = message
        conversations.loc[index, "step"] = "ask_time"

        write_all_sheets(patients, doctors, appointments, conversations)

        return "Enter appointment time in this format:\nHH:MM\nExample: 10:30"

    if step == "ask_time":
        if not is_valid_time(message):
            return "Invalid time format.\nPlease enter time like this:\nHH:MM\nExample: 10:30"

        conversations.loc[index, "time"] = message

        patient_name = str(conversations.loc[index, "patient_name"])
        doctor_id = str(conversations.loc[index, "doctor_id"])
        date = str(conversations.loc[index, "date"])
        time = str(conversations.loc[index, "time"])

        doctor_row = doctors[doctors["doctor_id"].astype(str) == doctor_id]

        if doctor_row.empty:
            return "Doctor not found. Please start again."

        doctor_name = doctor_row.iloc[0]["name"]

        clash = appointments[
            (appointments["doctor_id"].astype(str) == doctor_id) &
            (appointments["date"].astype(str) == date) &
            (appointments["time"].astype(str) == time) &
            (appointments["status"].astype(str).isin(["booked", "confirmed"]))
        ]

        if not clash.empty:
            return "Doctor is not free at this time.\nPlease enter another time in HH:MM format."

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

        appointments = pd.concat(
            [appointments, pd.DataFrame([new_appointment], dtype=object)],
            ignore_index=True
        )

        if phone not in patients["phone"].astype(str).values:
            new_patient = {
                "patient_id": str(len(patients) + 1),
                "name": patient_name,
                "phone": phone
            }
            patients = pd.concat(
                [patients, pd.DataFrame([new_patient], dtype=object)],
                ignore_index=True
            )

        conversations.loc[index, "step"] = "completed"

        write_all_sheets(patients, doctors, appointments, conversations)

        return f"Appointment successfully booked ✅\n\nPatient: {patient_name}\nDoctor: {doctor_name}\nDate: {date}\nTime: {time}\n\nThank you for booking with ABC Clinic."

    if step == "completed":
        if message == "1":
            otp = str(random.randint(1000, 9999))

            conversations.loc[index, "patient_name"] = ""
            conversations.loc[index, "step"] = "ask_name"
            conversations.loc[index, "otp"] = otp
            conversations.loc[index, "verified"] = "False"
            conversations.loc[index, "doctor_id"] = ""
            conversations.loc[index, "date"] = ""
            conversations.loc[index, "time"] = ""

            write_all_sheets(patients, doctors, appointments, conversations)

            return "Starting a new appointment booking.\nPlease enter your name."

        if message == "2":
            return "Reception will contact you soon. Thank you."

        return "You already have a completed booking.\n\nType:\n1. Book new appointment\n2. Contact reception"

    return "Something went wrong. Please start again."


@app.route("/")
def home():
    return jsonify({"message": "Clinic Pro WhatsApp Backend is running"})


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

    reply = process_chat_message(phone, message)

    return jsonify({"reply": reply})


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

            reply = process_chat_message(phone, text)

            print("PHONE:", phone)
            print("MESSAGE:", text)
            print("BOT REPLY:", reply)

    except Exception as e:
        print("Webhook error:", e)

    return "EVENT_RECEIVED", 200


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