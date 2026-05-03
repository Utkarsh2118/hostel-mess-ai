const byId = (id) => document.getElementById(id);

const postJSON = async (url, payload) => {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json();
};

const setupStudentPage = () => {
  const attendanceForm = byId("attendanceForm");
  const attendanceMsg = byId("attendanceMsg");
  const basicPredictionForm = byId("basicPredictionForm");
  const basicPredictionResult = byId("basicPredictionResult");
  const mealSlotInput = byId("mealSlotInput");
  const currentMealLabel = byId("currentMealLabel");
  const mealTimeHint = byId("mealTimeHint");
  const submitButton = byId("attendanceSubmitBtn");
  const mealWindowPanel = byId("mealWindowPanel");
  const timelineRows = document.querySelectorAll(".meal-timeline li");

  const mealWindows = [
    { meal: "Breakfast", start: 8 * 60, end: 10 * 60, label: "08:00 AM - 10:00 AM" },
    { meal: "Lunch", start: 12 * 60, end: 15 * 60, label: "12:00 PM - 03:00 PM" },
    { meal: "Tea", start: 17 * 60, end: 18 * 60, label: "05:00 PM - 06:00 PM" },
    { meal: "Dinner", start: 20 * 60, end: 22 * 60, label: "08:00 PM - 10:00 PM" },
  ];

  const getCurrentMeal = () => {
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    return mealWindows.find((item) => minutes >= item.start && minutes < item.end) || null;
  };

  const getNextMeal = () => {
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    return mealWindows.find((item) => minutes < item.start) || mealWindows[0];
  };

  const updateMealWindowUI = () => {
    const active = getCurrentMeal();

    timelineRows.forEach((row) => {
      const rowMeal = row.getAttribute("data-meal");
      row.classList.toggle("active", !!active && rowMeal === active.meal);
    });

    if (!currentMealLabel || !mealTimeHint || !submitButton || !mealWindowPanel || !mealSlotInput) {
      return;
    }

    if (active) {
      mealSlotInput.value = active.meal;
      currentMealLabel.textContent = `${active.meal} Window Open`;
      mealTimeHint.textContent = `Timing: ${active.label}`;
      submitButton.disabled = false;
      submitButton.title = "";
      mealWindowPanel.classList.remove("closed");
    } else {
      const nextMeal = getNextMeal();
      mealSlotInput.value = "Closed";
      currentMealLabel.textContent = "Mess Window Closed";
      mealTimeHint.textContent = `Next slot: ${nextMeal.meal} (${nextMeal.label})`;
      submitButton.disabled = true;
      submitButton.title = "Attendance opens only during meal timings.";
      mealWindowPanel.classList.add("closed");
    }
  };

  updateMealWindowUI();
  window.setInterval(updateMealWindowUI, 60000);

  if (attendanceForm) {
    attendanceForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      attendanceMsg.classList.remove("success", "error");

      try {
        const active = getCurrentMeal();
        if (!active) {
          attendanceMsg.textContent = "Attendance is closed right now. Please submit in the active meal window.";
          attendanceMsg.classList.add("error");
          return;
        }

        const data = Object.fromEntries(new FormData(attendanceForm).entries());
        const result = await postJSON("/mark_attendance", data);

        attendanceMsg.textContent = result.message || "Saved.";
        attendanceMsg.classList.add(result.success ? "success" : "error");

        if (result.success) {
          attendanceForm.reset();
        }
      } catch {
        attendanceMsg.textContent = "Could not save attendance. Please try again.";
        attendanceMsg.classList.add("error");
      }
    });
  }

  if (basicPredictionForm) {
    basicPredictionForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      basicPredictionResult.classList.remove("hidden");
      basicPredictionResult.innerHTML = "<p>Calculating forecast...</p>";

      try {
        const data = Object.fromEntries(new FormData(basicPredictionForm).entries());
        const result = await postJSON("/predict_basic", data);
        basicPredictionResult.innerHTML = `
          <h3><i class="fa-solid fa-chart-simple"></i> Prediction Result</h3>
          <div class="result-metrics">
            <div class="result-item">
              <p>Predicted Waste</p>
              <strong>${result.predicted_waste} kg</strong>
            </div>
            <div class="result-item">
              <p>Suggested Food</p>
              <strong>${result.suggested_food} kg</strong>
            </div>
            <div class="result-item">
              <p>Model Accuracy (R2)</p>
              <strong>${result.model_accuracy}</strong>
            </div>
          </div>
        `;
      } catch {
        basicPredictionResult.innerHTML =
          "<p class=\"error-msg\">Unable to generate prediction right now. Please try again.</p>";
      }
    });
  }
};

const setupAdminNavigation = () => {
  const navButtons = document.querySelectorAll(".nav-link");
  const sections = document.querySelectorAll(".page-section");

  navButtons.forEach((button) => {
    button.addEventListener("click", () => {
      navButtons.forEach((btn) => btn.classList.remove("active"));
      sections.forEach((section) => section.classList.remove("active"));

      button.classList.add("active");
      const targetId = button.dataset.target;
      const targetSection = byId(targetId);
      if (targetSection) {
        targetSection.classList.add("active");
      }
    });
  });
};

let wasteChart = null;

const renderChart = (rows) => {
  const canvas = byId("wasteChart");
  if (!canvas) {
    return;
  }

  const labels = rows.map((_, i) => `Day ${i + 1}`);
  const values = rows.map((row) => Number(row.waste_kg || 0));

  if (wasteChart) {
    wasteChart.destroy();
  }

  wasteChart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Waste (kg)",
          data: values,
          borderColor: "#0f9d76",
          backgroundColor: "rgba(15,157,118,0.15)",
          borderWidth: 2,
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: {
          labels: {
            font: { family: "Space Grotesk" },
          },
        },
      },
      scales: {
        x: {
          ticks: {
            font: { family: "Space Grotesk" },
          },
        },
        y: {
          beginAtZero: true,
          ticks: {
            font: { family: "Space Grotesk" },
          },
        },
      },
    },
  });
};

const loadSummary = async () => {
  const summary = await fetch("/api/summary").then((r) => r.json());

  byId("totalStudents").textContent = summary.total_students ?? 0;
  byId("predictedWaste").textContent = `${summary.predicted_waste ?? 0} kg`;
  byId("suggestedFood").textContent = `${summary.suggested_food ?? 0} kg`;
  byId("modelAccuracy").textContent = summary.r2 ?? 0;
};

const loadAttendanceLive = async () => {
  const meta = byId("attendanceLiveMeta");
  const tableBody = byId("attendanceTableBody");

  if (!meta || !tableBody) {
    return;
  }

  const payload = await fetch("/api/attendance/live").then((r) => r.json());
  const summary = payload.summary || {};

  byId("liveAttendanceCount").textContent = summary.total_entries ?? 0;
  byId("liveWillEatCount").textContent = summary.will_eat_count ?? 0;
  byId("liveSkipCount").textContent = summary.skip_count ?? 0;
  byId("livePendingCount").textContent = summary.pending_entries ?? 0;

  meta.textContent = `Date: ${payload.date || "Today"} | Unique students: ${summary.unique_students ?? 0}`;

  const rows = Array.isArray(payload.records) ? payload.records : [];
  tableBody.innerHTML = "";

  if (!rows.length) {
    tableBody.innerHTML = '<tr><td colspan="5">No attendance entries yet.</td></tr>';
    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.timestamp || "-"}</td>
      <td>${row.student_name || "-"}</td>
      <td>${row.meal_slot || "-"}</td>
      <td>${row.status || "-"}</td>
      <td>${row.finalized ? "Yes" : "No"}</td>
    `;
    tableBody.appendChild(tr);
  });
};

const loadTableAndChart = async () => {
  const rows = await fetch("/api/data").then((r) => r.json());
  const body = byId("dataTableBody");
  if (!body || !Array.isArray(rows)) {
    return;
  }

  body.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.students_present}</td>
      <td>${row.prepared_kg}</td>
      <td>${row.consumed_kg}</td>
      <td>${row.waste_kg}</td>
      <td>${row.is_weekend ? "Yes" : "No"}</td>
      <td>${row.is_exam_period ? "Yes" : "No"}</td>
      <td><button class="delete-btn" data-id="${row.id}">Delete</button></td>
    `;
    body.appendChild(tr);
  });

  document.querySelectorAll(".delete-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.id;
      await postJSON(`/api/data/${id}/delete`, {});
      await loadSummary();
      await loadTableAndChart();
    });
  });

  renderChart(rows);
};

const setupAddDataForm = () => {
  const form = byId("addDataForm");
  const message = byId("addDataMessage");
  if (!form) {
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    const result = await postJSON("/api/data/add", data);
    message.textContent = result.message || "Record added.";
    form.reset();
    await loadSummary();
    await loadTableAndChart();
  });
};

const setupPredictionForm = () => {
  const form = byId("predictionForm");
  const loader = byId("predictionLoader");
  const resultBox = byId("predictionResult");
  if (!form) {
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    loader.classList.remove("hidden");
    resultBox.classList.add("hidden");

    const data = Object.fromEntries(new FormData(form).entries());

    // Small delay so users can feel prediction processing visually.
    await new Promise((resolve) => setTimeout(resolve, 600));
    const result = await postJSON("/api/predict", data);

    loader.classList.add("hidden");
    resultBox.classList.remove("hidden");
    resultBox.innerHTML = `
      <strong>Predicted Waste:</strong> ${result.predicted_waste} kg<br>
      <strong>Suggested Food:</strong> ${result.suggested_food} kg<br>
      <strong>Model Accuracy (R2):</strong> ${result.r2}
    `;
  });
};

const appendChatBubble = (role, text, source = "", reason = "") => {
  const chatWindow = byId("chatWindow");
  if (!chatWindow) {
    return;
  }

  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role}`;
  const reasonTag = role === "bot" && source === "rule" && reason ? `: ${reason}` : "";
  const sourceTag = role === "bot" && source ? ` (${source.toUpperCase()}${reasonTag})` : "";
  bubble.textContent = `${text}${sourceTag}`;
  chatWindow.appendChild(bubble);
  chatWindow.scrollTop = chatWindow.scrollHeight;
};

const setupChatbot = () => {
  const form = byId("chatForm");
  const input = byId("chatInput");

  if (!form || !input) {
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) {
      return;
    }

    appendChatBubble("user", message);
    input.value = "";

    const result = await postJSON("/api/chatbot", { message });
    appendChatBubble(
      "bot",
      result.reply || "I could not process that query.",
      result.source || "",
      result.fallback_reason || ""
    );
  });
};

const setupRefresh = () => {
  const refreshBtn = byId("refreshDataBtn");
  if (!refreshBtn) {
    return;
  }

  refreshBtn.addEventListener("click", async () => {
    await loadSummary();
    await loadTableAndChart();
    await loadAttendanceLive();
  });
};

const setupFinalizeDay = () => {
  const finalizeBtn = byId("finalizeDayBtn");
  const finalizeMsg = byId("finalizeDayMsg");

  if (!finalizeBtn || !finalizeMsg) {
    return;
  }

  finalizeBtn.addEventListener("click", async () => {
    finalizeMsg.classList.remove("success", "error");
    finalizeMsg.textContent = "Finalizing today's attendance...";
    finalizeBtn.disabled = true;

    try {
      const result = await postJSON("/api/attendance/finalize-day", { is_exam_period: 0 });
      finalizeMsg.textContent = result.message || "Day finalized.";
      finalizeMsg.classList.add(result.success ? "success" : "error");

      await loadSummary();
      await loadTableAndChart();
      await loadAttendanceLive();
    } catch {
      finalizeMsg.textContent = "Day finalization failed. Please try again.";
      finalizeMsg.classList.add("error");
    } finally {
      finalizeBtn.disabled = false;
    }
  });
};

const setupMenuEditor = () => {
  const form = byId("menuUpdateForm");
  const message = byId("menuUpdateMsg");
  if (!form || !message) {
    return;
  }

  const mealInput = form.querySelector('select[name="meal"]');
  const itemsInput = form.querySelector('input[name="items"]');

  const loadMenu = async () => {
    try {
      const menuData = await fetch("/api/admin/menu").then((r) => r.json());
      const selectedMeal = mealInput.value || "Breakfast";
      const items = (menuData.menus && menuData.menus[selectedMeal]) || "";
      itemsInput.value = items;
    } catch {
      // Keep form usable even if menu fetch fails.
    }
  };

  mealInput.addEventListener("change", async () => {
    await loadMenu();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    message.classList.remove("success", "error");

    try {
      const data = Object.fromEntries(new FormData(form).entries());
      const result = await postJSON("/api/admin/menu", data);
      message.textContent = result.message || "Menu saved.";
      message.classList.add(result.success ? "success" : "error");
    } catch {
      message.textContent = "Unable to save menu right now.";
      message.classList.add("error");
    }
  });

  loadMenu();
};

const setupAdminPage = async () => {
  if (!document.body.classList.contains("admin-page")) {
    return;
  }

  setupAdminNavigation();
  setupAddDataForm();
  setupPredictionForm();
  setupChatbot();
  setupRefresh();
  setupFinalizeDay();
  setupMenuEditor();

  await loadSummary();
  await loadTableAndChart();
  await loadAttendanceLive();

  window.setInterval(async () => {
    await loadSummary();
    await loadAttendanceLive();
  }, 30000);
};

setupStudentPage();
setupAdminPage();
