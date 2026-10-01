let activeComplaint = null;
let cachedTopics = [];
let cachedRegions = [];
let queueRequestId = 0;
let similarRequestId = 0;
let classifyRequestId = 0;
let confirmRequestId = 0;
let selectionGeneration = 0;

const STATUS_LABELS = {pending: "в ожидании", needs_clarification: "нужно уточнение", confirmed: "подтверждено"};
const STATUS_CLASSES = {pending: "badge-pending", needs_clarification: "badge-clarification", confirmed: "badge-confirmed"};
const queueState = {view: "pending", regionId: "", priority: "", page: 1, pageSize: 10, pages: 1, items: [], counts: null};

async function pulseFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const cookie = document.cookie.split("; ").find(item => item.startsWith("pulse109_csrf="));
    if (cookie) headers.set("X-CSRF-Token", decodeURIComponent(cookie.split("=").slice(1).join("=")));
  }
  const response = await window.fetch.call(window, path, {...options, headers, credentials: "same-origin"});
  if (response.status === 401) window.location.assign("/");
  return response;
}

document.addEventListener("DOMContentLoaded", () => {
  initApp();
  setupEventListeners();
});

async function initApp() {
  await Promise.all([loadHealth(), loadRegions(), loadTopics()]);
  await Promise.all([loadStats(), loadQueue()]);
}

function setupEventListeners() {
  const textInput = document.getElementById("intake-text");
  const charNum = document.getElementById("char-num");
  textInput.addEventListener("input", () => {
    charNum.textContent = textInput.value.length;
  });

  document.getElementById("intake-form").addEventListener("submit", handleIntakeSubmit);
  document.getElementById("queue-refresh").addEventListener("click", loadQueue);
  document.querySelectorAll("[data-queue-view]").forEach(btn => {
    btn.addEventListener("click", () => {
      queueState.view = btn.dataset.queueView;
      queueState.page = 1;
      loadQueue();
    });
  });
  document.getElementById("queue-region-filter").addEventListener("change", (event) => {
    queueState.regionId = event.target.value;
    queueState.page = 1;
    loadQueue();
  });
  document.getElementById("queue-priority-filter").addEventListener("change", (event) => {
    queueState.priority = event.target.value;
    queueState.page = 1;
    loadQueue();
  });
  document.getElementById("queue-next").addEventListener("click", handleQueueNext);
  document.getElementById("queue-prev").addEventListener("click", () => {
    if (queueState.page > 1) {
      queueState.page -= 1;
      loadQueue();
    }
  });
  document.getElementById("queue-next-page").addEventListener("click", () => {
    if (queueState.page < queueState.pages) {
      queueState.page += 1;
      loadQueue();
    }
  });
  document.getElementById("btn-classify").addEventListener("click", handleClassify);
  document.getElementById("confirm-form").addEventListener("submit", handleConfirmSubmit);
  document.getElementById("confirm-topic").addEventListener("change", handleTopicChange);
  document.querySelectorAll("[data-stub-url]").forEach(button => {
    button.addEventListener("click", () => callStub(button.dataset.stubUrl, button.dataset.stubMethod));
  });
}

async function loadHealth() {
  try {
    const res = await pulseFetch("/api/health");
    const data = await res.json();
    document.getElementById("app-mode").textContent = data.mode;
    document.getElementById("app-training").textContent = data.training_status;
  } catch (err) {
    console.error("Health check error", err);
  }
}

async function loadRegions() {
  try {
    const res = await pulseFetch("/api/regions");
    const data = await res.json();
    cachedRegions = data.regions || [];
    const select = document.getElementById("intake-region");
    select.innerHTML = '<option value="">-- Выберите регион --</option>';
    cachedRegions.forEach(r => {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = `${r.name_ru} (${r.name_kk})`;
      select.appendChild(opt);
    });
    const queueFilter = document.getElementById("queue-region-filter");
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = "Все регионы";
    queueFilter.replaceChildren(
      allOption,
      ...cachedRegions.map(r => {
        const opt = document.createElement("option");
        opt.value = r.id;
        opt.textContent = r.name_ru;
        return opt;
      })
    );
    queueFilter.value = queueState.regionId;
  } catch (err) {
    console.error("Failed to load regions", err);
  }
}

async function loadTopics() {
  try {
    const res = await pulseFetch("/api/topics");
    const data = await res.json();
    cachedTopics = data.topics || [];
    const select = document.getElementById("confirm-topic");
    select.innerHTML = '<option value="">-- Выберите тему --</option>';
    cachedTopics.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = `${t.name_ru} / ${t.name_kk}`;
      select.appendChild(opt);
    });
  } catch (err) {
    console.error("Failed to load topics", err);
  }
}

async function loadStats() {
  try {
    const res = await pulseFetch("/api/stats");
    const data = await res.json();
    document.getElementById("stat-total").textContent = data.total_complaints;
    document.getElementById("stat-pending").textContent = data.pending_count;
    document.getElementById("stat-confirmed").textContent = data.confirmed_count;
    document.getElementById("stat-clarification").textContent = data.clarification_count ?? "—";

    const breakdown = document.getElementById("topics-breakdown");
    breakdown.innerHTML = "";
    const topicMap = Object.fromEntries(cachedTopics.map(t => [t.id, t.name_ru]));
    for (const [topicId, count] of Object.entries(data.by_topic || {})) {
      const row = document.createElement("div");
      row.className = "breakdown-row";
      const name = topicMap[topicId] || topicId;
      row.append(textElement("span", name), textElement("strong", count));
      breakdown.appendChild(row);
    }
  } catch (err) {
    console.error("Failed to load stats", err);
  }
}

function queueRequestUrl() {
  const params = new URLSearchParams({
    view: queueState.view,
    page: String(queueState.page),
    page_size: String(queueState.pageSize),
  });
  if (queueState.regionId) params.set("region_id", queueState.regionId);
  if (queueState.priority) params.set("priority", queueState.priority);
  return `/api/complaints?${params.toString()}`;
}

function queueHasNext() {
  const index = queueState.items.findIndex(c => c.id === activeComplaint?.id);
  if (index === -1) return queueState.items.length > 0 || queueState.page < queueState.pages;
  return index < queueState.items.length - 1 || queueState.page < queueState.pages;
}

function updateQueueControls() {
  document.querySelectorAll("[data-queue-view]").forEach(btn => {
    const active = btn.dataset.queueView === queueState.view;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
    const countEl = btn.querySelector(".queue-count");
    if (countEl) countEl.textContent = queueState.counts ? String(queueState.counts[btn.dataset.queueView] ?? "—") : "—";
  });
  document.getElementById("queue-page-info").textContent = `Страница ${queueState.page} из ${queueState.pages}`;
  document.getElementById("queue-prev").disabled = queueState.page <= 1;
  document.getElementById("queue-next-page").disabled = queueState.page >= queueState.pages;
  document.getElementById("queue-next").disabled = !queueHasNext();
}

async function handleQueueNext() {
  const index = queueState.items.findIndex(c => c.id === activeComplaint?.id);
  if (index === -1 && queueState.items.length) {
    selectComplaint(queueState.items[0]);
    return;
  }
  if (index >= 0 && index < queueState.items.length - 1) {
    selectComplaint(queueState.items[index + 1]);
    return;
  }
  if (queueState.page < queueState.pages) {
    queueState.page += 1;
    await loadQueue();
    if (queueState.items.length) selectComplaint(queueState.items[0]);
  }
}

async function loadQueue() {
  const requestId = ++queueRequestId;
  const list = document.getElementById("queue-list");
  const status = document.getElementById("queue-status");
  const errorBox = document.getElementById("queue-error");
  list.setAttribute("aria-busy", "true");
  status.textContent = "Загружаем очередь…";
  errorBox.hidden = true;

  let payload;
  try {
    const res = await pulseFetch(queueRequestUrl());
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const items = Array.isArray(data?.items) ? data.items : null;
    if (!items) throw new Error("Invalid queue response");
    const rows = items.map(c => {
      if (!c || typeof c.id !== "string" || typeof c.text !== "string") throw new Error("Invalid queue response");
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "queue-item";
      button.dataset.complaintId = c.id;
      const selected = activeComplaint?.id === c.id;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
      const shortText = c.text.length > 55 ? c.text.substring(0, 55) + "..." : c.text;
      const preview = textElement("span", shortText);
      preview.title = c.text;
      const regionObj = cachedRegions.find(r => r.id === c.region_id);
      const regionName = regionObj ? regionObj.name_ru : (c.region_id || "—");
      const eventDate = String(c.received_at || c.ingested_at || "").slice(0, 10);
      const statusValue = typeof c.decision_status === "string" ? c.decision_status : "";
      button.append(
        preview,
        textElement("span", `${regionName} · ${eventDate || "—"}`, "queue-meta"),
        textElement("span", STATUS_LABELS[statusValue] || "—", `badge ${STATUS_CLASSES[statusValue] || "badge-pending"}`)
      );
      const urgency = c.priority === "urgent" ? "срочно" : (c.proposed_priority === "urgent" ? "предложено: срочно" : "");
      if (urgency) button.append(textElement("span", urgency, "queue-urgency"));
      button.addEventListener("click", () => selectComplaint(c));
      item.appendChild(button);
      return item;
    });
    payload = {
      items,
      rows,
      page: Number.isInteger(data.page) ? data.page : queueState.page,
      pages: Number.isInteger(data.pages) ? data.pages : 1,
      total: Number.isInteger(data.total) ? data.total : items.length,
      counts: data.view_counts && typeof data.view_counts === "object" ? data.view_counts : null,
    };
  } catch (err) {
    if (requestId !== queueRequestId) return;
    list.setAttribute("aria-busy", "false");
    const hasRows = list.querySelector("button") !== null;
    if (hasRows) {
      list.dataset.stale = "true";
    } else {
      delete list.dataset.stale;
      list.replaceChildren();
    }
    errorBox.textContent = hasRows
      ? "Не удалось обновить очередь. Показанные данные могли устареть."
      : "Не удалось загрузить очередь. Повторите попытку.";
    errorBox.hidden = false;
    status.textContent = "";
    return;
  }
  if (requestId !== queueRequestId) return;
  list.setAttribute("aria-busy", "false");
  delete list.dataset.stale;
  queueState.items = payload.items;
  queueState.page = payload.page;
  queueState.pages = payload.pages;
  queueState.counts = payload.counts;
  if (!payload.rows.length) {
    list.replaceChildren(textElement("li", "Очередь пуста", "empty-state"));
    status.textContent = "Очередь пуста.";
    updateQueueControls();
    return;
  }
  list.replaceChildren(...payload.rows);
  status.textContent = `Показано обращений: ${payload.total}`;
  updateQueueControls();
}

function selectComplaint(c) {
  selectionGeneration += 1;
  activeComplaint = c;
  document.getElementById("active-id").textContent = c.id;
  document.getElementById("active-text").textContent = c.text;
  const regionObj = cachedRegions.find(r => r.id === c.region_id);
  document.getElementById("active-region").textContent = regionObj ? regionObj.name_ru : c.region_id;
  document.getElementById("active-origin").textContent = c.data_origin === "synthetic" ? "Демонстрационные данные" : c.data_origin === "organizer" ? "Данные организаторов" : "Обращение гражданина";

  const badge = document.getElementById("active-status-badge");
  const statusValue = typeof c.decision_status === "string" ? c.decision_status : "";
  badge.textContent = STATUS_LABELS[statusValue] || "—";
  badge.className = `badge ${STATUS_CLASSES[statusValue] || "badge-pending"}`;

  document.getElementById("btn-classify").disabled = false;
  document.getElementById("btn-confirm").disabled = c.decision_status === "needs_clarification";
  document.getElementById("confirm-error").style.display = "none";
  document.getElementById("confirm-success").style.display = "none";

  // Pre-fill confirm form; unknown urgency (null or legacy values) forces an explicit human choice.
  const confirmedPriority = ["normal", "urgent"].includes(c.priority) ? c.priority : "";
  const proposedPriority = ["normal", "urgent"].includes(c.proposed_priority) ? c.proposed_priority : "";
  document.getElementById("confirm-topic").value = c.topic || c.proposed_topic || "";
  document.getElementById("confirm-service").value = c.service_id || c.proposed_service_id || "";
  document.getElementById("confirm-priority").value = confirmedPriority || proposedPriority;

  // Load proposal / similar if already present
  if (c.proposed_topic) {
    renderProposal({
      topic: c.proposed_topic,
      service_id: c.proposed_service_id,
      priority: c.proposed_priority
    });
  } else {
    document.getElementById("proposal-content").innerHTML = '<p class="empty-state">Нажмите «Получить рекомендацию», чтобы определить категорию.</p>';
  }
  loadSimilar(c.id);
  syncClarificationControls();
  refreshClarifications(c.id);
  document.querySelectorAll("#queue-list button").forEach(button => {
    const selected = button.dataset.complaintId === c.id;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  updateQueueControls();
}

async function handleIntakeSubmit(e) {
  e.preventDefault();
  const errorBox = document.getElementById("intake-error");
  const btn = document.getElementById("btn-submit-intake");
  if (btn.disabled) return;
  errorBox.style.display = "none";

  const region = document.getElementById("intake-region").value;
  const lang = document.getElementById("intake-lang").value;
  const text = document.getElementById("intake-text").value.trim();

  if (!region) {
    showError(errorBox, "Выберите регион");
    return;
  }
  if (!text) {
    showError(errorBox, "Введите текст обращения");
    return;
  }

  btn.disabled = true;
  try {
    const res = await pulseFetch("/api/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, region_id: region, language: lang }),
    });
    if (!res.ok) {
      const errData = await res.json();
      showError(errorBox, errData.detail || "Проверьте регион и текст обращения");
      return;
    }
    const created = await res.json();
    document.getElementById("intake-form").reset();
    document.getElementById("char-num").textContent = "0";
    await loadStats();
    await loadQueue();
    // Fetch full complaint and select
    try {
      const fullRes = await pulseFetch(`/api/complaints/${created.id}`);
      if (!fullRes.ok) throw new Error(`HTTP ${fullRes.status}`);
      const fullData = await fullRes.json();
      if (!fullData || !fullData.complaint || typeof fullData.complaint.id !== "string") throw new Error("Invalid complaint response");
      selectComplaint(fullData.complaint);
    } catch (followErr) {
      console.error("Failed to load the created complaint", followErr);
      showError(errorBox, "Обращение зарегистрировано, но автоматический выбор не удался. Найдите его в очереди.");
    }
  } catch (err) {
    showError(errorBox, "Сетевая ошибка при отправке обращения");
  } finally {
    btn.disabled = false;
  }
}

async function handleClassify() {
  if (!activeComplaint) return;
  const complaint = activeComplaint;
  const requestId = ++classifyRequestId;
  const generation = selectionGeneration;
  const btn = document.getElementById("btn-classify");
  const errorBox = document.getElementById("proposal-error");
  errorBox.hidden = true;
  btn.disabled = true;
  let proposal;
  try {
    const res = await pulseFetch(`/api/complaints/${complaint.id}/classify`, { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || !data.proposal || typeof data.proposal !== "object" || Array.isArray(data.proposal)) throw new Error("Invalid classification response");
    proposal = data.proposal;
  } catch (err) {
    console.error("Classification error", err);
    if (requestId === classifyRequestId && selectionGeneration === generation) {
      errorBox.hidden = false;
    }
    return;
  } finally {
    if (requestId === classifyRequestId) btn.disabled = false;
  }
  if (requestId !== classifyRequestId) return;
  if (selectionGeneration !== generation) return;
  renderProposal(proposal);
  if (proposal.topic) {
    document.getElementById("confirm-topic").value = proposal.topic;
  }
  if (proposal.service_id) {
    document.getElementById("confirm-service").value = proposal.service_id;
  }
  if (proposal.priority) {
    document.getElementById("confirm-priority").value = proposal.priority;
  }
  loadSimilar(complaint.id);
}

function renderProposal(proposal) {
  const container = document.getElementById("proposal-content");
  if (!proposal || !proposal.topic) {
    container.innerHTML = `
      <div class="proposal-pill" style="background:#fee2e2;color:#991b1b;">Тема не распознана</div>
      <p style="font-size:0.85rem;margin-top:0.4rem;">Недостаточно информации · выберите тему вручную.</p>
    `;
    return;
  }
  const tObj = cachedTopics.find(t => t.id === proposal.topic);
  const topicName = tObj ? tObj.name_ru : proposal.topic;
  const topic = textElement("div", "Предлагаемая тема: ");
  topic.appendChild(textElement("span", topicName, "proposal-pill"));
  const details = textElement("div", "Служба: ");
  details.style.cssText = "font-size:0.85rem;margin-top:0.3rem;";
  details.append(textElement("strong", proposal.service_id || "—"), " | Приоритет: ",
    textElement("strong", proposal.priority));
  container.replaceChildren(topic, details);
}

async function loadSimilar(complaintId) {
  const requestId = ++similarRequestId;
  const container = document.getElementById("similar-list");
  container.replaceChildren(textElement("p", "Загружаем похожие обращения…", "empty-state"));
  let items;
  try {
    const res = await pulseFetch(`/api/complaints/${complaintId}/similar?limit=5`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || !Array.isArray(data.candidates)) throw new Error("Invalid similar response");
    items = data.candidates.map(cand => {
      if (!cand || typeof cand.complaint_id !== "string" || typeof cand.excerpt !== "string") throw new Error("Invalid similar response");
      const div = document.createElement("div");
      div.className = "similar-item";
      const excerpt = document.createElement("div");
      excerpt.append(textElement("strong", `[${cand.complaint_id}]`), ` ${cand.excerpt}`);
      const statusText = STATUS_LABELS[cand.decision_status] || "На проверке";
      const originText = cand.origin === "synthetic" ? "Демонстрационные данные" : cand.origin === "organizer" ? "Данные организаторов" : "Обращение гражданина";
      const metadata = textElement("div", `Статус: ${statusText} | Источник: ${originText}`);
      metadata.style.cssText = "color:#64748b;font-size:0.75rem;";
      div.append(excerpt, metadata);
      if (cand.resolution_text) {
        const resolution = textElement("div", `Решение: ${cand.resolution_text}`);
        resolution.style.color = "#059669";
        div.appendChild(resolution);
      }
      return div;
    });
  } catch (err) {
    if (requestId !== similarRequestId) return;
    container.replaceChildren(textElement("p", "Не удалось загрузить похожие обращения.", "empty-state"));
    return;
  }
  if (requestId !== similarRequestId) return;
  if (!items.length) {
    container.replaceChildren(textElement("p", "Похожих обращений не найдено.", "empty-state"));
    return;
  }
  container.replaceChildren(...items);
}

function handleTopicChange() {
  const topicId = document.getElementById("confirm-topic").value;
  const topicObj = cachedTopics.find(t => t.id === topicId);
  if (topicObj && topicObj.default_service) {
    document.getElementById("confirm-service").value = topicObj.default_service;
  }
}

async function handleConfirmSubmit(e) {
  e.preventDefault();
  if (!activeComplaint) return;
  const btn = document.getElementById("btn-confirm");
  if (btn.disabled) return;
  const complaint = activeComplaint;
  const generation = selectionGeneration;
  const errBox = document.getElementById("confirm-error");
  const succBox = document.getElementById("confirm-success");
  errBox.style.display = "none";
  succBox.style.display = "none";

  const topic = document.getElementById("confirm-topic").value;
  const service_id = document.getElementById("confirm-service").value.trim();
  const priority = document.getElementById("confirm-priority").value;

  if (!topic) { showError(errBox, "Выберите тему"); return; }
  if (!service_id) { showError(errBox, "Укажите ответственную службу"); return; }
  if (!priority) { showError(errBox, "Выберите приоритет"); return; }

  const requestId = ++confirmRequestId;
  btn.disabled = true;
  try {
    const res = await pulseFetch(`/api/complaints/${complaint.id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, service_id, priority }),
    });
    if (!res.ok) {
      const err = await res.json();
      showError(errBox, err.detail || "Ошибка подтверждения");
      return;
    }
    const data = await res.json();
    if (!data || !data.complaint || typeof data.complaint !== "object" || typeof data.complaint.id !== "string") {
      showError(errBox, "Получен некорректный ответ сервера. Повторите попытку.");
      return;
    }
    if (selectionGeneration === generation) {
      selectComplaint(data.complaint);
    }
    succBox.textContent = `Решение по обращению ${data.complaint.id} сохранено.`;
    succBox.style.display = "block";
    await loadStats();
    await loadQueue();
  } catch (err) {
    showError(errBox, "Сетевая ошибка подтверждения");
  } finally {
    if (requestId === confirmRequestId) btn.disabled = false;
  }
}

function textElement(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}

function showError(box, msg) {
  box.textContent = msg;
  box.style.display = "block";
}

async function callStub(url, method = "GET") {
  const out = document.getElementById("stub-output");
  out.style.display = "block";
  out.textContent = `Запрос: ${method} ${url}...\n`;
  try {
    const res = await pulseFetch(url, { method });
    const json = await res.json();
    out.textContent += `HTTP ${res.status} ${res.statusText}\n${JSON.stringify(json, null, 2)}`;
  } catch (err) {
    out.textContent += `Ошибка: ${err.message}`;
  }
}
