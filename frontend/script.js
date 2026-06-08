const API_URL = "https://clinic-pro-whatsapp.onrender.com";

let allAppointments = [];

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
      <td><button class="danger" onclick="deleteDoctor('${doc.doctor_id}')">Delete</button></td>
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
  if (!confirm("Are you sure you want to delete this doctor?")) return;

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

async function confirmAppointment(id) {
  const response = await fetch(`${API_URL}/confirm/${id}`, {
    method: "POST"
  });

  const result = await response.json();

  if (result.success) {
    alert(result.whatsapp_message);
    loadAppointments();
  } else {
    alert(result.message);
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
  } else {
    alert(result.message);
  }
}

async function cancelAppointment(id) {
  const response = await fetch(`${API_URL}/cancel/${id}`, {
    method: "POST"
  });

  const result = await response.json();

  if (result.success) {
    alert(result.whatsapp_message || "Appointment cancelled");
    loadAppointments();
  } else {
    alert(result.message);
  }
}

async function loadAppointments() {
  const response = await fetch(`${API_URL}/appointments`);
  allAppointments = await response.json();

  updateStats(allAppointments);
  applyFilters();
}

function updateStats(appointments) {
  document.getElementById("totalCount").textContent = appointments.length;

  document.getElementById("bookedCount").textContent =
    appointments.filter(a => a.status === "booked").length;

  document.getElementById("confirmedCount").textContent =
    appointments.filter(a => a.status === "confirmed").length;

  document.getElementById("completedCount").textContent =
    appointments.filter(a => a.status === "completed").length;

  document.getElementById("cancelledCount").textContent =
    appointments.filter(a => a.status === "cancelled").length;
}

function applyFilters() {
  const search = document.getElementById("searchInput").value.toLowerCase();
  const status = document.getElementById("statusFilter").value;

  let filtered = allAppointments.filter(app => {
    const text = `
      ${app.patient_name}
      ${app.phone}
      ${app.doctor_name}
      ${app.date}
      ${app.time}
      ${app.status}
    `.toLowerCase();

    const matchesSearch = text.includes(search);
    const matchesStatus = status === "all" || app.status === status;

    return matchesSearch && matchesStatus;
  });

  renderAppointments(filtered);
}

function getStatusBadge(status) {
  return `<span class="badge ${status}">${status}</span>`;
}

function renderAppointments(appointments) {
  const table = document.getElementById("appointmentsTable");
  table.innerHTML = "";

  if (appointments.length === 0) {
    table.innerHTML = `
      <tr>
        <td colspan="8" class="empty">No appointments found</td>
      </tr>
    `;
    return;
  }

  // Sort appointments: Active (booked/confirmed) first.
  // Within active: earliest date & time first (ascending chronological).
  // Within inactive (completed/cancelled): latest date & time first (descending chronological).
  appointments.sort((a, b) => {
    const aActive = a.status === "booked" || a.status === "confirmed";
    const bActive = b.status === "booked" || b.status === "confirmed";

    if (aActive && !bActive) return -1;
    if (!aActive && bActive) return 1;

    const dateTimeA = `${a.date} ${a.time}`;
    const dateTimeB = `${b.date} ${b.time}`;

    if (aActive) {
      return dateTimeA.localeCompare(dateTimeB);
    } else {
      return dateTimeB.localeCompare(dateTimeA);
    }
  });

  appointments.forEach(app => {
    const row = document.createElement("tr");

    let actionButtons = `<span class="done-text">Done</span>`;

    if (app.status === "booked") {
      actionButtons = `
        <button onclick="confirmAppointment('${app.appointment_id}')">Confirm</button>
        <button class="danger" onclick="cancelAppointment('${app.appointment_id}')">Cancel</button>
      `;
    }

    if (app.status === "confirmed") {
      actionButtons = `
        <button class="success" onclick="completeAppointment('${app.appointment_id}')">Complete</button>
        <button class="danger" onclick="cancelAppointment('${app.appointment_id}')">Cancel</button>
      `;
    }

    row.innerHTML = `
      <td>${app.appointment_id}</td>
      <td>${app.patient_name}</td>
      <td>${app.phone}</td>
      <td>${app.doctor_name}</td>
      <td>${app.date}</td>
      <td>${app.time}</td>
      <td>${getStatusBadge(app.status)}</td>
      <td>${actionButtons}</td>
    `;

    table.appendChild(row);
  });
}

loadDoctors();
loadAppointments();