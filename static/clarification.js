const CLARIFICATION_DEFAULT_QUESTIONS = {
  unknown_place: "Уточните, пожалуйста, где возникла проблема: адрес или ближайший ориентир.",
  unclear_event: "Опишите, пожалуйста, подробнее, что именно произошло.",
  insufficient_detail: "Добавьте, пожалуйста, больше деталей: что, где и когда произошло.",
  multiple_problems: "Уточните, пожалуйста, какую из проблем нужно рассмотреть в первую очередь.",
  other: "",
};

let clarificationBusy = false;

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("clarification-reason").addEventListener("change", applyDefaultClarificationQuestion);
  document.getElementById("clarification-form").addEventListener("submit", handleClarificationRequest);
  document.getElementById("btn-save-supplement").addEventListener("click", handleClarificationResponse);
  document.getElementById("btn-resume").addEventListener("click", handleResume);
  applyDefaultClarificationQuestion();
  syncClarificationControls();
});

function applyDefaultClarificationQuestion() {
  const reason = document.getElementById("clarification-reason").value;
  document.getElementById("clarification-question").value = CLARIFICATION_DEFAULT_QUESTIONS[reason] || "";
}

function syncClarificationControls() {
  const status = activeComplaint ? activeComplaint.decision_status : null;
  document.getElementById("btn-request-clarification").disabled = clarificationBusy || status !== "pending";
  document.getElementById("btn-save-supplement").disabled = clarificationBusy || status !== "needs_clarification";
  document.getElementById("btn-resume").disabled = clarificationBusy || status !== "needs_clarification";
}

async function postClarification(complaintId, action, body) {
  const options = { method: "POST" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const res = await pulseFetch(`/api/complaints/${complaintId}/${action}`, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const error = new Error("Clarification action failed");
    error.detail = err.detail || "Не удалось выполнить действие. Повторите попытку.";
    throw error;
  }
  const data = await res.json();
  if (!data || !data.complaint || typeof data.complaint.id !== "string") {
    throw new Error("Invalid clarification response");
  }
  return data;
}

async function runClarificationAction(complaint, generation, action, body, errorId, successId, successText) {
  const errorBox = document.getElementById(errorId);
  const successBox = document.getElementById(successId);
  errorBox.style.display = "none";
  successBox.style.display = "none";
  clarificationBusy = true;
  syncClarificationControls();
  try {
    const data = await postClarification(complaint.id, action, body);
    successBox.textContent = successText;
    successBox.style.display = "block";
    if (selectionGeneration === generation) {
      selectComplaint(data.complaint);
    }
    await loadStats();
    await loadQueue();
  } catch (err) {
    showError(errorBox, err.detail || "Не удалось выполнить действие. Повторите попытку.");
  } finally {
    clarificationBusy = false;
    syncClarificationControls();
  }
}

async function handleClarificationRequest(e) {
  e.preventDefault();
  if (!activeComplaint || clarificationBusy) return;
  const complaint = activeComplaint;
  const generation = selectionGeneration;
  const reason = document.getElementById("clarification-reason").value;
  const question = document.getElementById("clarification-question").value.trim();
  const errorBox = document.getElementById("clarification-error");
  const successBox = document.getElementById("clarification-success");
  errorBox.style.display = "none";
  successBox.style.display = "none";
  if (!question) {
    showError(errorBox, "Введите вопрос гражданину");
    return;
  }
  await runClarificationAction(
    complaint,
    generation,
    "clarification",
    { reason, question },
    "clarification-error",
    "clarification-success",
    "Причина и вопрос сохранены. Заявка помечена «Нужно уточнение»."
  );
}

async function handleClarificationResponse() {
  if (!activeComplaint || clarificationBusy) return;
  const complaint = activeComplaint;
  const generation = selectionGeneration;
  const text = document.getElementById("clarification-supplement").value.trim();
  const errorBox = document.getElementById("clarification-response-error");
  const successBox = document.getElementById("clarification-response-success");
  errorBox.style.display = "none";
  successBox.style.display = "none";
  if (!text) {
    showError(errorBox, "Введите полученное уточнение");
    return;
  }
  await runClarificationAction(
    complaint,
    generation,
    "clarification-response",
    { text },
    "clarification-response-error",
    "clarification-response-success",
    "Полученное уточнение сохранено отдельно от исходного текста."
  );
}

async function handleResume() {
  if (!activeComplaint || clarificationBusy) return;
  await runClarificationAction(
    activeComplaint,
    selectionGeneration,
    "resume",
    undefined,
    "clarification-response-error",
    "clarification-response-success",
    "Заявка возвращена в обработку (pending)."
  );
}

async function refreshClarifications(complaintId) {
  const generation = selectionGeneration;
  const block = document.getElementById("active-clarification");
  const target = document.getElementById("active-clarification-text");
  block.hidden = true;
  try {
    const res = await pulseFetch(`/api/complaints/${complaintId}`);
    if (!res.ok) return;
    const data = await res.json();
    if (selectionGeneration !== generation || !activeComplaint || activeComplaint.id !== complaintId) return;
    const events = Array.isArray(data.events) ? data.events : [];
    const latest = type => [...events].reverse().find(event => event.event_type === type);
    const parse = event => {
      try {
        return JSON.parse(event.payload);
      } catch (err) {
        return {};
      }
    };
    const parts = [];
    const questionEvent = latest("clarification_requested");
    if (questionEvent) {
      const question = parse(questionEvent).question;
      if (typeof question === "string" && question) parts.push(`Вопрос: ${question}`);
    }
    const receivedEvent = latest("clarification_received");
    if (receivedEvent) {
      const text = parse(receivedEvent).text;
      if (typeof text === "string" && text) parts.push(`Получено: ${text}`);
    }
    if (!parts.length) return;
    target.textContent = parts.join(" ");
    block.hidden = false;
  } catch (err) {
    // The detail fetch failure leaves the clarification block hidden; the card itself is unchanged.
  }
}
