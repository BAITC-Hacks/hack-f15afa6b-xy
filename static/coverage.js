/* Read-only organizer evidence. No complaint text or model claims. */
(() => {
  const byId = id => document.getElementById(id);
  const statusLabels = {
    present_in_header: "Есть в заголовке",
    candidate_unverified: "Кандидат: смысл не проверен",
    not_observed: "Нет явного поля",
  };
  let rows = [];
  let requestVersion = 0;
  const number = value => value == null ? "Не проверено" : value.toLocaleString("ru-RU");

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function fieldLine(label, field, supplied) {
    const line = node("p");
    line.append(node("strong", label + ": "));
    line.append(document.createTextNode(field ? statusLabels[field.status] || "Не проверено"
      : supplied ? "Не проверено" : "Нет файла"));
    if (field?.columns?.length) line.title = "Проверены заголовки: " + field.columns.join(", ");
    return line;
  }

  function renderRows() {
    const selectedSupply = byId("coverage-supply").value;
    const visible = rows.filter(r => selectedSupply === "all" || r.supply_status === selectedSupply);
    const body = byId("coverage-rows");
    body.replaceChildren();
    for (const region of visible) {
      const supplied = region.supply_status === "supplied";
      const tr = node("tr");
      const name = node("th", region.name_ru);
      name.scope = "row";
      name.append(node("small", region.name_kk));
      const files = node("td");
      files.append(node("span", supplied ? `${region.file_count} CSV · получены` : "Нет файла",
        supplied ? "coverage-badge supplied" : "coverage-badge missing"));
      const count = node("td", supplied ? number(region.record_count) : "Нет файла");
      count.append(node("small", region.record_count == null ? "Объём неизвестен" : "Подсчитано локально; не уникальные обращения"));
      const history = node("td");
      history.append(node("p", "Период: " + (region.period ?? "не проверен")));
      history.append(node("p", "Обновление: " + (region.last_updated ?? "не проверено")));
      const text = node("td");
      text.append(fieldLine("Текст", region.fields.original_text, supplied),
        fieldLine("Тема", region.fields.category, supplied));
      const outcomes = node("td");
      outcomes.append(fieldLine("Язык", region.fields.language, supplied),
        fieldLine("Решение", region.fields.resolution, supplied));
      tr.append(name, files, count, history, text, outcomes);
      body.append(tr);
    }
    byId("coverage-empty").hidden = visible.length > 0;
    byId("coverage-table-wrap").hidden = visible.length === 0;
    byId("coverage-status").textContent = `Показано регионов: ${visible.length}. Счётчики сверху относятся ко всему полученному пакету.`;
  }

  async function loadCoverage() {
    const version = ++requestVersion;
    const panel = byId("coverage-panel");
    panel.setAttribute("aria-busy", "true");
    byId("coverage-content").hidden = true;
    byId("coverage-error").hidden = true;
    byId("coverage-status").textContent = "Загружаем сведения о покрытии…";
    byId("coverage-source").textContent = "Источник: инвентарь и матрица полученного пакета.";
    for (const key of ["supplied", "missing", "files"]) byId("coverage-" + key).textContent = "—";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const region = byId("coverage-region").value;
      const suffix = region ? "?region_id=" + encodeURIComponent(region) : "";
      const response = await pulseFetch("/api/data-coverage" + suffix, {signal: controller.signal});
      if (!response.ok) throw new Error("coverage unavailable");
      const data = await response.json();
      if (version !== requestVersion) return;
      if (data.evidence_kind !== "organizer_metadata" || !Array.isArray(data.regions)) throw new Error("invalid evidence");
      const summary = data.summary;
      byId("coverage-supplied").textContent = `${summary.supplied_regions} / ${summary.required_regions}`;
      byId("coverage-missing").textContent = summary.missing_regions;
      byId("coverage-files").textContent = summary.supplied_files;
      // The all-regions response owns the option list; filtered responses never replace it.
      if (!region) {
        const select = byId("coverage-region");
        select.replaceChildren(node("option", "Все регионы"));
        select.firstElementChild.value = "";
        for (const item of data.regions) {
          const option = node("option", item.name_ru);
          option.value = item.region_id;
          select.append(option);
        }
        select.disabled = false;
      }
      rows = data.regions;
      renderRows();
      byId("coverage-content").hidden = false;
      byId("coverage-source").textContent = `Источник: инвентарь (${data.as_of.inventory}) и матрица (${data.as_of.matrix}). Дата проверки не равна дате обновления обращений.`;
    } catch (_) {
      if (version !== requestVersion) return;
      rows = [];
      for (const key of ["supplied", "missing", "files"]) byId("coverage-" + key).textContent = "—";
      byId("coverage-content").hidden = true;
      byId("coverage-rows").replaceChildren();
      byId("coverage-error").textContent = "Не удалось проверить покрытие. Сведения недоступны — это не отсутствие данных. Нажмите «Обновить покрытие».";
      byId("coverage-error").hidden = false;
      byId("coverage-status").textContent = "Покрытие недоступно.";
    } finally {
      clearTimeout(timeout);
      if (version === requestVersion) panel.setAttribute("aria-busy", "false");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    byId("coverage-region").addEventListener("change", loadCoverage);
    byId("coverage-supply").addEventListener("change", () => {
      if (!byId("coverage-content").hidden) renderRows();
    });
    byId("coverage-retry").addEventListener("click", loadCoverage);
    loadCoverage();
  });
})();
