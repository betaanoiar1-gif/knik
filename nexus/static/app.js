const I18N = {
  ar: {
    loginTitle: "رمز لوحة التحكم",
    loginHelp: "هذا الرمز يحمي لوحة التحكم. ليس مفتاح KiosAPI.",
    loginButton: "دخول",
    navOverview: "نظرة عامة",
    navEngines: "المحركات",
    navKios: "KiosAPI",
    navSkills: "المهارات",
    navTasks: "المهام",
    navSessions: "الجلسات",
    navDiagnostics: "التشخيص",
    overviewTitle: "لوحة التشغيل",
    overviewLede: "المحركات الأصلية تبقى عمليات مستقلة. فشل أحدهما لا يوقف الآخر.",
    activeTask: "المهمة الحالية",
    kiosSummary: "KiosAPI",
    refresh: "تحديث",
    saveSettings: "حفظ الإعدادات",
    saveKey: "حفظ المفتاح",
    clearKey: "مسح المفتاح",
    probeDry: "فحص صياغة الطلب",
    probeLive: "فحص اتصال حقيقي",
    skillsLede: "الملفات الأصلية لا تُعدَّل. التثبيت رابط رمزي إلى مجلد الاكتشاف الخاص بالمحرك.",
    validateProject: "تحقق من المسار",
    runTask: "تشغيل المهمة",
    cancelTask: "إلغاء المهمة",
    footer: "NEXUS لا يعيد كتابة Free Claude Code أو OpenCode أو Skills. الاتصال الحقيقي بـ KiosAPI لا يُعلَن ناجحًا إلا بعد طلب تم تنفيذه.",
    start: "تشغيل",
    stop: "إيقاف",
    install: "تثبيت",
    uninstall: "إزالة",
    configured: "المفتاح محفوظ",
    missing: "لا يوجد مفتاح",
    liveUnverified: "لم يُتحقق من اتصال حي",
  },
  en: {
    loginTitle: "Control-plane token",
    loginHelp: "This token protects the dashboard. It is not the KiosAPI key.",
    loginButton: "Enter",
    navOverview: "Overview",
    navEngines: "Engines",
    navKios: "KiosAPI",
    navSkills: "Skills",
    navTasks: "Tasks",
    navSessions: "Sessions",
    navDiagnostics: "Diagnostics",
    overviewTitle: "Operations",
    overviewLede: "The original engines stay independent processes. One failure does not stop the other.",
    activeTask: "Current task",
    kiosSummary: "KiosAPI",
    refresh: "Refresh",
    saveSettings: "Save settings",
    saveKey: "Save key",
    clearKey: "Clear key",
    probeDry: "Check request shape",
    probeLive: "Live probe",
    skillsLede: "Original files are not modified. Install creates a symlink in the engine discovery directory.",
    validateProject: "Validate path",
    runTask: "Run task",
    cancelTask: "Cancel task",
    footer: "NEXUS does not rewrite Free Claude Code, OpenCode, or Skills. A live KiosAPI call is reported only after it actually runs.",
    start: "Start",
    stop: "Stop",
    install: "Install",
    uninstall: "Remove",
    configured: "key stored",
    missing: "no key",
    liveUnverified: "live call not verified",
  },
};

const state = {
  lang: localStorage.getItem("nexus-lang") || "ar",
  token: sessionStorage.getItem("nexus-token") || "",
  engines: [],
  skills: [],
  settings: null,
  taskId: null,
  poll: null,
};

const $ = (selector) => document.querySelector(selector);

function t(key) {
  return (I18N[state.lang] && I18N[state.lang][key]) || I18N.en[key] || key;
}

function applyLanguage() {
  document.documentElement.lang = state.lang;
  document.documentElement.dir = state.lang === "ar" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  $("#lang").textContent = state.lang === "ar" ? "English" : "العربية";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    $("#login").classList.remove("hidden");
    throw new Error("unauthorized");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || data.error || data;
    const message = typeof detail === "string" ? detail : detail.message || JSON.stringify(detail);
    throw new Error(message);
  }
  return data;
}

function show(panel) {
  document.querySelectorAll(".panel").forEach((node) => node.classList.add("hidden"));
  const target = document.getElementById(panel);
  if (target) target.classList.remove("hidden");
}

function engineCard(engine) {
  const ready = engine.running ? "on" : engine.runtime_ready ? "wait" : "";
  return `<article class="engine">
    <header><strong>${engine.name || engine.id}</strong><span class="dot ${ready}"></span></header>
    <p>${engine.runtime_detail || ""}</p>
    <p><span class="tag">${engine.kind || ""}</span> <span class="tag">${engine.real_engine === false ? "test-double" : "upstream"}</span></p>
    <p>${(engine.capabilities || []).join(" · ")}</p>
    <p>${engine.last_error || ""}</p>
  </article>`;
}

async function refreshEngines() {
  const data = await api("/api/engines");
  state.engines = data.engines || [];
  $("#engine-cards").innerHTML = state.engines.map(engineCard).join("");
  $("#engine-admin").innerHTML = state.engines.map((engine) => `
    <article class="engine">
      <header><strong>${engine.name || engine.id}</strong><span>${engine.running ? "running" : "stopped"}</span></header>
      <p>${engine.runtime_detail || ""}</p>
      <p>credential: ${engine.credential_configured ? t("configured") : t("missing")} · ${t("liveUnverified")}</p>
      <p>endpoint: ${engine.endpoint || "—"}</p>
      <div class="actions">
        <button type="button" data-action="start-engine" data-engine="${engine.id}">${t("start")}</button>
        <button type="button" data-action="stop-engine" data-engine="${engine.id}">${t("stop")}</button>
      </div>
      <pre class="log">${JSON.stringify(engine, null, 2)}</pre>
    </article>`).join("");
  const select = $("#task-engine");
  select.innerHTML = state.engines.map((engine) => `<option value="${engine.id}">${engine.name || engine.id}</option>`).join("");
}

async function refreshSettings() {
  const data = await api("/api/settings");
  state.settings = data.settings;
  const form = $("#settings-form");
  Object.entries(data.settings).forEach(([key, value]) => {
    const field = form.elements[key];
    if (!field) return;
    field.value = Array.isArray(value) ? value.join("\n") : value;
  });
  $("#key-state").textContent = data.secrets.kios_api_key_configured ? t("configured") : t("missing");
  $("#kios-summary").innerHTML = Object.entries(data.kios).map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("");
}

async function refreshSkills() {
  const project = $("#project-path").value.trim();
  const query = project ? `?project=${encodeURIComponent(project)}` : "";
  const data = await api("/api/skills" + query);
  state.skills = data.skills || [];
  const installed = data.installed || {};
  $("#skill-list").innerHTML = state.skills.map((skill) => {
    const fcc = skill.compatibility["free-claude-code"];
    const oc = skill.compatibility.opencode;
    return `<article class="skill">
      <header><strong>${skill.name}</strong><span class="tag">${skill.category}</span></header>
      <p>${skill.description}</p>
      <p>FCC: ${fcc.install_path}</p>
      <p>OpenCode: ${oc.ok ? oc.install_path : oc.note}</p>
      <div class="actions">
        <button type="button" data-action="install-skill" data-skill="${skill.name}" data-engines="free-claude-code${oc.ok ? ",opencode" : ""}">${t("install")}</button>
        <button type="button" data-action="uninstall-skill" data-skill="${skill.name}">${t("uninstall")}</button>
        <span>${installed[skill.name] ? "installed" : ""}</span>
      </div>
    </article>`;
  }).join("");
  $("#task-skills").innerHTML = state.skills.map((skill) => `<option value="${skill.name}">${skill.name}</option>`).join("");
}

async function refreshSessions() {
  const data = await api("/api/sessions");
  const tasks = await api("/api/tasks");
  $("#session-list").innerHTML = (data.sessions || []).map((session) => {
    const related = (tasks.tasks || []).filter((task) => task.session_id === session.id);
    return `<article class="engine"><strong>${session.engine_id}</strong><p>${session.id}</p><p>${session.project}</p><pre class="log">${related.map((task) => task.status + " " + task.id).join("\n") || "—"}</pre></article>`;
  }).join("") || "<p>—</p>";
}

async function refreshDiagnostics() {
  const data = await api("/api/diagnostics");
  $("#diagnostics").textContent = JSON.stringify(data, null, 2);
}

function selectedSkills() {
  return Array.from($("#task-skills").selectedOptions).map((option) => option.value);
}

async function pollTask() {
  if (!state.taskId) return;
  const task = await api(`/api/tasks/${state.taskId}`);
  $("#task-meta").textContent = `${task.engine_id} · ${task.status} · ${task.id}`;
  $("#task-log").textContent = (task.events || []).map((event) => `[${event.stream}] ${event.text}`).join("\n");
  $("#active-task").textContent = $("#task-log").textContent || "—";
  if (task.status === "queued" || task.status === "running") {
    state.poll = setTimeout(pollTask, 700);
  }
}

async function onClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  try {
    if (action === "show") show(button.dataset.panel);
    if (action === "set-language") {
      state.lang = state.lang === "ar" ? "en" : "ar";
      localStorage.setItem("nexus-lang", state.lang);
      applyLanguage();
      await refreshEngines();
    }
    if (action === "refresh-engines") await refreshEngines();
    if (action === "start-engine") {
      await api(`/api/engines/${button.dataset.engine}/start`, { method: "POST" });
      await refreshEngines();
    }
    if (action === "stop-engine") {
      await api(`/api/engines/${button.dataset.engine}/stop`, { method: "POST" });
      await refreshEngines();
    }
    if (action === "clear-secret") {
      await api("/api/kiosapi/secret", { method: "DELETE" });
      await refreshSettings();
    }
    if (action === "probe-dry" || action === "probe-live") {
      const data = await api("/api/kiosapi/probe", {
        method: "POST",
        body: JSON.stringify({ dry_run: action === "probe-dry" }),
      });
      $("#kios-output").textContent = JSON.stringify(data, null, 2);
    }
    if (action === "refresh-skills") await refreshSkills();
    if (action === "install-skill" || action === "uninstall-skill") {
      const project = $("#project-path").value.trim();
      await api(action === "install-skill" ? "/api/skills/install" : "/api/skills/uninstall", {
        method: "POST",
        body: JSON.stringify({
          name: button.dataset.skill,
          project,
          engines: (button.dataset.engines || "free-claude-code,opencode").split(","),
        }),
      });
      await refreshSkills();
    }
    if (action === "validate-project") {
      const data = await api("/api/projects/validate", {
        method: "POST",
        body: JSON.stringify({ path: $("#project-path").value.trim() }),
      });
      $("#task-meta").textContent = data.path;
    }
    if (action === "cancel-task" && state.taskId) {
      await api(`/api/tasks/${state.taskId}/cancel`, { method: "POST" });
      await pollTask();
    }
    if (action === "refresh-sessions") await refreshSessions();
    if (action === "refresh-diagnostics") await refreshDiagnostics();
  } catch (error) {
    const target = $("#task-log");
    if (target && document.getElementById("tasks") && !document.getElementById("tasks").classList.contains("hidden")) {
      target.textContent = error.message;
    } else {
      $("#kios-output").textContent = error.message;
    }
  }
}

document.addEventListener("click", onClick);
document.addEventListener("submit", async (event) => {
  const form = event.target;
  const action = form.dataset.action;
  if (!action) return;
  event.preventDefault();
  if (action === "login") {
    state.token = $("#login-token").value.trim();
    sessionStorage.setItem("nexus-token", state.token);
    try {
      await api("/api/health");
      await api("/api/settings");
      $("#login").classList.add("hidden");
      await boot();
    } catch (error) {
      $("#login-error").textContent = error.message;
    }
  }
  if (action === "save-settings") {
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.allowed_roots = payload.allowed_roots.split("\n").map((item) => item.trim()).filter(Boolean);
    ["kios_rpm", "kios_max_retries", "kios_timeout_seconds", "kios_context_tokens", "kios_max_output_tokens"].forEach((key) => {
      if (payload[key] !== undefined) payload[key] = Number(payload[key]);
    });
    await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    await refreshSettings();
    $("#kios-output").textContent = "saved";
  }
  if (action === "save-secret") {
    const value = $("#api-key").value;
    await api("/api/kiosapi/secret", { method: "PUT", body: JSON.stringify({ api_key: value }) });
    $("#api-key").value = "";
    await refreshSettings();
  }
  if (action === "submit-task") {
    const created = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        engine_id: $("#task-engine").value,
        prompt: $("#task-prompt").value,
        project: $("#project-path").value.trim(),
        skills: selectedSkills(),
      }),
    });
    state.taskId = created.id;
    localStorage.setItem("nexus-project", $("#project-path").value.trim());
    await pollTask();
  }
});

async function boot() {
  applyLanguage();
  if (document.querySelector('meta[name="nexus-token-injected"]').content === "1") {
    const note = document.createElement("p");
    note.className = "lede";
    note.textContent = state.lang === "ar"
      ? "وضع المعاينة: رمز اللوحة مضمّن في هذه الصفحة. لا تدخل مفتاح KiosAPI حقيقيًا إذا كان الرابط مشتركًا."
      : "Preview mode: the control token is embedded in this page. Do not enter a real KiosAPI key if the link is shared.";
    document.querySelector("main").prepend(note);
  }
  const injected = document.querySelector('meta[name="nexus-ui-token"]').content;
  if (!state.token && injected) {
    state.token = injected;
    sessionStorage.setItem("nexus-token", injected);
  }
  const project = localStorage.getItem("nexus-project");
  if (project) $("#project-path").value = project;
  try {
    await refreshSettings();
    await refreshEngines();
    await refreshSkills();
    $("#login").classList.add("hidden");
  } catch (error) {
    if (error.message !== "unauthorized") $("#kios-output").textContent = error.message;
  }
}

boot();
