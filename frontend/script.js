const API_URL = "http://127.0.0.1:5055";

async function loadDoctors() {
  const response = await fetch(`${API_URL}/doctors`);
  const doctors = await response.json();

  const doctorSelect = document.getElementById("doctor");
  const doctorsTable = document.getElementById("doctorsTable");

  doctorSelect.innerHTML = "";
  doctorsTable.innerHTML = "";

  doctors.forEach(doc => {
    const option = document.createElement("option");
    option.value = doc.doctor_id;
    option.textContent = `${doc.name} - ${doc.specialization}`;
    doctorSelect.appendChild(option);

    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${doc.doctor_id}</td>
      <td>${doc.name}</td>
      <td>${doc.specialization}</td>
      <td><button onclick="deleteDoctor('${doc.doctor_id}')">Delete</button></td>
    `;
    doctorsTable.appendChild(row);
  });
}

async function addDoctor() {
  const data = {
    name: document.getElementById("doctorName").value,
    specialization: document.getElementById("specialization").value
  };

  const response = await fetch(`${API_URL}/add-doctor`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data)
  });

  const result = await response.json();

  if (result.success) {
    alert("Doctor added successfully");
    document.getElementById("doctorName").value = "";
    document.getElementById("specialization").value = "";
    loadDoctors();
  } else {
    alert(result.message);
  }
}

async function deleteDoctor(id) {
  const response = await fetch(`${API_URL}/delete-doctor/${id}`, {
    method: "POST"
  });

  const result = await response.json();

  if (result.success) {
    alert("Doctor deleted successfully");
    loadDoctors();
  } else {
    alert(result.message);
  }
}

async function bookAppointment() {
  const data = {
    patient_name: document.getElementById("patientName").value,
    phone: document.getElementById("phone").value,
    doctor_id: document.getElementById("doctor").value,
    date: document.getElementById("date").value,
    time: document.getElementById("time").value
  };

  const response = await fetch(`${API_URL}/book`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data)
  });

  const result = await response.json();
  const message = document.getElementById("message");

  if (result.success) {
    message.style.color = "green";
    message.textContent = "Appointment booked successfully!";
    loadAppointments();
  } else {
    message.style.color = "red";
    message.textContent = result.message;
  }
}

async function completeAppointment(id) {
  const response = await fetch(`${API_URL}/complete/${id}`, {
    method: "POST"
  });

  const result = await response.json();

  if (result.success) {
    alert(result.whatsapp_message);
    loadAppointments();
  }
}

async function cancelAppointment(id) {
  const response = await fetch(`${API_URL}/cancel/${id}`, {
    method: "POST"
  });

  const result = await response.json();

  if (result.success) {
    alert("Appointment cancelled");
    loadAppointments();
  }
}

async function loadAppointments() {
  const response = await fetch(`${API_URL}/appointments`);
  const appointments = await response.json();

  const table = document.getElementById("appointmentsTable");
  table.innerHTML = "";

  appointments.forEach(app => {
    const row = document.createElement("tr");

    let actionButtons = "Done";

    if (app.status === "booked") {
      actionButtons = `
        <button onclick="completeAppointment('${app.appointment_id}')">Complete Visit</button>
        <button onclick="cancelAppointment('${app.appointment_id}')">Cancel</button>
      `;
    }

    row.innerHTML = `
      <td>${app.appointment_id}</td>
      <td>${app.patient_name}</td>
      <td>${app.phone}</td>
      <td>${app.doctor_name}</td>
      <td>${app.date}</td>
      <td>${app.time}</td>
      <td>${app.status}</td>
      <td>${actionButtons}</td>
    `;

    table.appendChild(row);
  });
}

loadDoctors();
loadAppointments();