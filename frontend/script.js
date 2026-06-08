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

function formatDateHeader(dateStr) {
  try {
    const parts = dateStr.split('-');
    if (parts.length !== 3) return dateStr;
    const d = new Date(parts[0], parts[1] - 1, parts[2]);
    const today = new Date();
    today.setHours(0,0,0,0);
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const dTime = d.getTime();
    const todayTime = today.getTime();
    const tomorrowTime = tomorrow.getTime();
    
    const options = { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' };
    const formatted = d.toLocaleDateString('en-US', options);
    
    if (dTime === todayTime) {
      return `📅 Today — ${formatted}`;
    } else if (dTime === tomorrowTime) {
      return `🌅 Tomorrow — ${formatted}`;
    }
    return `🗓️ ${formatted}`;
  } catch (e) {
    return dateStr;
  }
}

function to12Hour(timeStr) {
  try {
    const parts = timeStr.split(':');
    let hour = parseInt(parts[0], 10);
    const minute = parts[1];
    const ampm = hour >= 12 ? 'PM' : 'AM';
    hour = hour % 12;
    hour = hour ? hour : 12;
    return `${hour}:${minute} ${ampm}`;
  } catch (e) {
    return timeStr;
  }
}

function updateHeaderDate() {
  const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
  const todayStr = new Date().toLocaleDateString('en-US', options);
  const display = document.getElementById("currentDateDisplay");
  if (display) {
    display.innerHTML = `Real-time booking & scheduling • <strong>${todayStr}</strong>`;
  }
}

function renderAppointments(appointments) {
  const container = document.getElementById("appointmentsContainer");
  container.innerHTML = "";

  if (appointments.length === 0) {
    container.innerHTML = `<div class="empty">No appointments found</div>`;
    return;
  }

  // Split active and inactive
  const active = appointments.filter(a => a.status === "booked" || a.status === "confirmed");
  const inactive = appointments.filter(a => a.status === "completed" || a.status === "cancelled");

  // Sort active: ascending chronological (earliest first)
  active.sort((a, b) => {
    return `${a.date} ${a.time}`.localeCompare(`${b.date} ${b.time}`);
  });

  // Sort inactive: descending chronological (most recent first)
  inactive.sort((a, b) => {
    return `${b.date} ${b.time}`.localeCompare(`${a.date} ${a.time}`);
  });

  // Render Active Grouped by Date
  if (active.length > 0) {
    const dateGroups = {};
    active.forEach(app => {
      if (!dateGroups[app.date]) dateGroups[app.date] = [];
      dateGroups[app.date].push(app);
    });

    const sortedDates = Object.keys(dateGroups).sort();

    sortedDates.forEach(date => {
      const groupApps = dateGroups[date];
      
      const groupDiv = document.createElement("div");
      groupDiv.className = "date-group";
      
      groupDiv.innerHTML = `
        <div class="date-group-header">
          <span>${formatDateHeader(date)}</span>
          <span class="badge-count">${groupApps.length} appointment${groupApps.length > 1 ? 's' : ''}</span>
        </div>
        <div class="table-wrapper">
          <table>
            <thead>
              <tr>
                <th style="width: 80px;">ID</th>
                <th style="width: 120px;">Time</th>
                <th>Patient</th>
                <th>WhatsApp Phone</th>
                <th>Doctor</th>
                <th>Status</th>
                <th style="width: 200px; text-align: right; padding-right: 24px;">Actions</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      `;
      
      const tbody = groupDiv.querySelector("tbody");
      groupApps.forEach(app => {
        const row = document.createElement("tr");
        
        let actionButtons = "";
        if (app.status === "booked") {
          actionButtons = `
            <button onclick="confirmAppointment('${app.appointment_id}')">Confirm</button>
            <button class="danger" onclick="cancelAppointment('${app.appointment_id}')">Cancel</button>
          `;
        } else if (app.status === "confirmed") {
          actionButtons = `
            <button class="success" onclick="completeAppointment('${app.appointment_id}')">Complete</button>
            <button class="danger" onclick="cancelAppointment('${app.appointment_id}')">Cancel</button>
          `;
        }

        row.innerHTML = `
          <td><strong>#${app.appointment_id}</strong></td>
          <td><strong>${to12Hour(app.time)}</strong></td>
          <td>${app.patient_name}</td>
          <td>${app.phone}</td>
          <td>${app.doctor_name}</td>
          <td>${getStatusBadge(app.status)}</td>
          <td style="text-align: right; padding-right: 24px;">${actionButtons}</td>
        `;
        tbody.appendChild(row);
      });
      
      container.appendChild(groupDiv);
    });
  }

  // Render Inactive History List at the bottom
  if (inactive.length > 0) {
    const historyDiv = document.createElement("div");
    historyDiv.className = "date-group";
    
    historyDiv.innerHTML = `
      <div class="date-group-header" style="color: var(--text-secondary); background-color: #f1f5f9; border-top: 2px solid var(--border-color);">
        <span>📜 Past & Completed History</span>
        <span class="badge-count" style="background-color: #e2e8f0; color: var(--text-secondary);">${inactive.length} item${inactive.length > 1 ? 's' : ''}</span>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th style="width: 80px;">ID</th>
              <th style="width: 150px;">Date</th>
              <th style="width: 120px;">Time</th>
              <th>Patient</th>
              <th>WhatsApp Phone</th>
              <th>Doctor</th>
              <th>Status</th>
              <th style="width: 200px; text-align: right; padding-right: 24px;">Actions</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
    `;
    
    const tbody = historyDiv.querySelector("tbody");
    inactive.forEach(app => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>#${app.appointment_id}</td>
        <td>${app.date}</td>
        <td>${to12Hour(app.time)}</td>
        <td>${app.patient_name}</td>
        <td>${app.phone}</td>
        <td>${app.doctor_name}</td>
        <td>${getStatusBadge(app.status)}</td>
        <td style="text-align: right; padding-right: 24px;"><span class="done-text">Archived</span></td>
      `;
      tbody.appendChild(row);
    });
    
    container.appendChild(historyDiv);
  }
}

// Initialization
updateHeaderDate();
loadDoctors();
loadAppointments();