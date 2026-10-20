const API = "/api/v1/support";
const TOKEN_KEY = "dots_support_auth_token";
const LEGACY_TOKEN_KEY = "dots_support_token";
const LANG_KEY = "dots_support_language";
const $ = (selector) => document.querySelector(selector);
const messages = $("#messages");
const ticketList = $("#ticket-list");
const languageSelect = $("#language");
let token = localStorage.getItem(TOKEN_KEY);
let authMode = "login";
let busy = false;
let eventController = null;
let feedbackByMessage = new Map();
let messageRecords = new Map();
let hasEarlierMessages = false;
let pendingRefresh = false;
const MESSAGE_PAGE_SIZE = 100;
const authCopy = {
  en: { eyebrow: "PLAYER ACCOUNT", loginTitle: "Sign in to support", registerTitle: "Create player account", hint: "Your reports and conversation stay with your account across devices.", email: "Email", name: "Display name (optional)", password: "Password", login: "Sign in", register: "Create account", toRegister: "New player? Create an account", toLogin: "Already registered? Sign in", logout: "Sign out", passwordHint: "Use 8–64 characters with uppercase, lowercase, a number and a special character." },
  es: { eyebrow: "CUENTA DE JUGADOR", loginTitle: "Acceder al soporte", registerTitle: "Crear cuenta de jugador", hint: "Tus informes y mensajes siguen vinculados a tu cuenta en otros dispositivos.", email: "Correo", name: "Nombre (opcional)", password: "Contraseña", login: "Entrar", register: "Crear cuenta", toRegister: "¿Nuevo jugador? Crea una cuenta", toLogin: "¿Ya tienes cuenta? Entra", logout: "Salir", passwordHint: "Usa 8–64 caracteres con mayúsculas, minúsculas, un número y un símbolo." },
  zh: { eyebrow: "玩家账号", loginTitle: "登录玩家支持", registerTitle: "注册玩家账号", hint: "对话和报告与账号绑定，换设备登录后仍可查看。", email: "邮箱", name: "昵称（可选）", password: "密码", login: "登录", register: "创建账号", toRegister: "还没有账号？注册", toLogin: "已有账号？登录", logout: "退出登录", passwordHint: "密码需 8–64 位，包含大小写字母、数字和特殊字符。" }
};
function at(key) { return (authCopy[languageSelect.value] || authCopy.en)[key]; }
function showAuth() {
  $("#player-auth").hidden = false;
  $("#support-content").hidden = true;
  $("#player-logout").hidden = true;
}
function showSupport() {
  $("#player-auth").hidden = true;
  $("#support-content").hidden = false;
  $("#player-logout").hidden = false;
}
function renderAuth() {
  $("#auth-eyebrow").textContent = at("eyebrow");
  $("#auth-title").textContent = at(authMode === "login" ? "loginTitle" : "registerTitle");
  $("#auth-hint").textContent = authMode === "register" ? at("passwordHint") : at("hint");
  $("#auth-email-label").textContent = at("email");
  $("#auth-name-label").textContent = at("name");
  $("#auth-password-label").textContent = at("password");
  $("#auth-submit").textContent = at(authMode);
  $("#auth-switch").textContent = at(authMode === "login" ? "toRegister" : "toLogin");
  $("#player-logout").textContent = at("logout");
  $("#player-name-row").hidden = authMode !== "register";
  $("#player-password").autocomplete = authMode === "register" ? "new-password" : "current-password";
}

const copy = {
  en: { welcome: "Hi! I’m the official DOTS Survivors AI support assistant. Ask me about published game information, or tell me what went wrong so the team can investigate.", sent: "Report sent. You can check its status here.", failed: "Could not complete that request. Please try again.", deleted: "Your conversation and reports were deleted.", deleteConfirm: "Delete this conversation and all its reports? This cannot be undone.", autoTicket: "A report was opened for the support team.", noReports: "No reports yet.", sessionLost: "Your previous session expired. A new one has started.", source: "Source" },
  es: { welcome: "¡Hola! Soy el asistente oficial de IA de DOTS Survivors. Pregúntame sobre la información publicada del juego o cuéntame qué falló para que el equipo lo investigue.", sent: "Informe enviado. Puedes consultar su estado aquí.", failed: "No se pudo completar la solicitud. Inténtalo de nuevo.", deleted: "Se eliminaron la conversación y los informes.", deleteConfirm: "¿Eliminar esta conversación y todos sus informes? No se puede deshacer.", autoTicket: "Se abrió un informe para el equipo de soporte.", noReports: "Todavía no hay informes.", sessionLost: "La sesión anterior caducó. Se inició una nueva.", source: "Fuente" },
  zh: { welcome: "你好！我是《DOTS Survivors》官方 AI 客服。你可以询问已发布的游戏信息，或描述遇到的问题，让团队核查。", sent: "报告已提交，可在这里查看状态。", failed: "请求未能完成，请重试。", deleted: "对话和相关报告已删除。", deleteConfirm: "确定删除此对话和全部报告吗？此操作无法撤销。", autoTicket: "已转交人工客服，完整对话会随工单提供给团队。", noReports: "暂无报告。", sessionLost: "之前的会话已过期，已开始新会话。", source: "来源" }
};
const ui = {
  es: { sidebarLabel: "AYUDA AL JUGADOR / CONTROL DE MISIÓN", openChannel: "CANAL ABIERTO", hero: "¿Necesitas ayuda ahí fuera?", heroCopy: "Pregunta sobre el juego, informa de un problema o consulta la respuesta del equipo.", signal: "SOPORTE DISPONIBLE", steam: "Ver en Steam ↗", pageTitle: "Ayuda al jugador", language: "Idioma", staff: "Equipo ↗", channel: "CANAL 01", talk: "Habla con soporte", ai: "Asistente IA", requirements: "Requisitos", weapons: "Armas", achievements: "Logros", yourMessage: "Tu mensaje", messagePlaceholder: "Cuéntanos en qué podemos ayudarte…", send: "Enviar", privacyNote: "Soporte oficial con IA. Las respuestas se basan en información publicada y el equipo revisa los informes. No compartas contraseñas ni datos de pago.", somethingBroke: "SI ALGO FALLA", reportIssue: "Informar de un problema", reportCopy: "Cuéntanos qué pasó. Incluye la versión del juego y tu dispositivo si los conoces.", createReport: "Crear informe", yourReports: "TUS INFORMES", refresh: "Actualizar", dataControl: "TUS DATOS", yourConversation: "Tu conversación", retention: "La sesión y sus informes se conservan hasta 30 días. Puedes borrarlos ahora.", delete: "Eliminar mi conversación", issueReport: "INFORME DE PROBLEMA", whatHappenedTitle: "Cuéntanos qué pasó", issueType: "Tipo de problema", bug: "Error", achievement: "Logro", save: "Guardado o Steam Cloud", performance: "Rendimiento", other: "Otro", whatHappened: "¿Qué pasó?", reportPlaceholder: "¿Qué esperabas y qué ocurrió?", gameVersion: "Versión del juego", device: "Dispositivo / SO", ifKnown: "Si lo sabes", reportPrivacy: "Evita los datos personales. Puedes seguir el informe en esta sesión.", sendReport: "Enviar informe" },
  zh: { sidebarLabel: "玩家支持 / 任务控制台", openChannel: "频道已开启", hero: "遇到问题了吗？", heroCopy: "询问游戏信息、报告问题，或查看客服团队的回复。", signal: "客服频道在线", steam: "在 Steam 查看 ↗", pageTitle: "玩家支持", language: "语言", staff: "员工入口 ↗", channel: "频道 01", talk: "联系支持", ai: "AI 助手", requirements: "配置要求", weapons: "武器", achievements: "成就", yourMessage: "你的消息", messagePlaceholder: "描述你需要的帮助…", send: "发送", privacyNote: "官方 AI 客服依据已发布资料回答，团队会审查问题报告。请勿提供密码或付款信息。", somethingBroke: "遇到故障", reportIssue: "报告问题", reportCopy: "告诉团队发生了什么。如有游戏版本和设备信息，也请一并提供。", createReport: "创建报告", yourReports: "我的报告", refresh: "刷新", dataControl: "数据管理", yourConversation: "我的对话", retention: "本会话及报告最多保留 30 天，你可以立即删除。", delete: "删除我的对话", issueReport: "问题报告", whatHappenedTitle: "描述发生的情况", issueType: "问题类型", bug: "故障", achievement: "成就", save: "存档或云同步", performance: "性能", other: "其他", whatHappened: "发生了什么？", reportPlaceholder: "你预期发生什么？实际发生了什么？", gameVersion: "游戏版本", device: "设备 / 系统", ifKnown: "如已知", reportPrivacy: "请勿填写个人信息。可在此浏览器会话中查看报告进度。", sendReport: "提交报告" }
};
function t(key) { return (copy[languageSelect.value] || copy.en)[key]; }
const extraCopy = {
  en: { human: "Support team", active: "A support team member is handling this conversation. AI replies are paused until the report is resolved.", helpful: "Helpful", unhelpful: "Needs work", thanks: "Thanks for your feedback.", earlier: "Load earlier messages" },
  es: { human: "Equipo de soporte", active: "Una persona atiende esta conversación. La IA volverá cuando se resuelva el informe.", helpful: "Útil", unhelpful: "Necesita mejorar", thanks: "Gracias por tu opinión.", earlier: "Cargar mensajes anteriores" },
  zh: { human: "人工客服", active: "人工客服正在处理此对话。工单解决前，AI 回复已暂停。", helpful: "有帮助", unhelpful: "没帮助", thanks: "加载更早消息" }
};
function tx(key) { return extraCopy[languageSelect.value]?.[key] || extraCopy.en[key]; }
function applyLanguage() {
  const translated = ui[languageSelect.value] || {};
  document.documentElement.lang = languageSelect.value;
  document.title = `${translated.pageTitle || "Player support"} · DOTS Survivors`;
  document.querySelectorAll("[data-i18n]").forEach((node) => { if (!node.dataset.defaultText) node.dataset.defaultText = node.textContent; node.textContent = translated[node.dataset.i18n] || node.dataset.defaultText; });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => { if (!node.dataset.defaultPlaceholder) node.dataset.defaultPlaceholder = node.placeholder; node.placeholder = translated[node.dataset.i18nPlaceholder] || node.dataset.defaultPlaceholder; });
  const prompts = { en: ["What are the minimum system requirements?", "Which weapons are in the game?", "How do I report an achievement issue?"], es: ["¿Cuáles son los requisitos mínimos?", "¿Qué armas hay en el juego?", "¿Cómo informo de un problema con un logro?"], zh: ["最低配置要求是什么？", "游戏里有哪些武器？", "如何报告成就问题？"] };
  document.querySelectorAll("[data-prompt]").forEach((button, index) => { button.dataset.prompt = prompts[languageSelect.value][index]; });
  $("#load-earlier").textContent = tx("earlier");
  renderAuth();
}
function toast(message) { const box = $("#toast"); box.textContent = message; box.classList.add("visible"); clearTimeout(toast.timer); toast.timer = setTimeout(() => box.classList.remove("visible"), 4000); }
async function api(path, options = {}) {
  const requestToken = token;
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.headers || {}) };
  const response = await fetch(API + path, { ...options, headers });
  if (!response.ok) {
    if ((response.status === 401 || response.status === 403) && token === requestToken) {
      token = null; localStorage.removeItem(TOKEN_KEY); resetMessages(); showAuth();
    }
    const payload = await response.json().catch(() => ({}));
    const error = new Error(payload.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}
function renderMessage(role, content, sources = [], id = null) {
  const item = document.createElement("article"); item.className = `message ${role}`;
  const label = document.createElement("div"); label.className = "message-label"; label.textContent = role === "player" ? "YOU" : role === "staff" || role === "handoff" ? tx("human") : "DOTS SUPPORT AI";
  const bubble = document.createElement("div"); bubble.className = "bubble"; bubble.textContent = content;
  item.append(label, bubble);
  if (sources.length) {
    const links = document.createElement("div"); links.className = "sources";
    for (const source of sources) {
      const a = document.createElement("a"); a.textContent = `${t("source")}: ${source.title} ↗`; a.href = source.url; a.target = "_blank"; a.rel = "noopener noreferrer"; links.append(a);
      if (source.image_url?.startsWith("/api/v1/support/images/")) {
        const figure = document.createElement("figure"); figure.className = "source-image";
        const imageLink = document.createElement("a"); imageLink.href = source.image_url; imageLink.target = "_blank"; imageLink.rel = "noopener noreferrer";
        const image = document.createElement("img"); image.src = source.image_url; image.alt = source.image_alt || source.title; image.loading = "lazy";
        imageLink.append(image); figure.append(imageLink, document.createElement("figcaption"));
        figure.querySelector("figcaption").textContent = source.image_alt || source.title;
        links.append(figure);
      }
    }
    item.append(links);
  }
  if (role === "assistant" && id) {
    const buttons = document.createElement("div"); buttons.className = "feedback-actions";
    for (const [rating, key] of [["positive", "helpful"], ["negative", "unhelpful"]]) {
      const button = document.createElement("button"); button.type = "button"; button.textContent = tx(key);
      button.setAttribute("aria-pressed", feedbackByMessage.get(id) === rating ? "true" : "false");
      button.addEventListener("click", async () => {
        try { await api(`/messages/${id}/feedback`, { method: "PUT", body: JSON.stringify({ rating }) }); feedbackByMessage.set(id, rating); buttons.querySelectorAll("button").forEach((node) => node.setAttribute("aria-pressed", String(node === button))); toast(tx("thanks")); }
        catch (error) { toast(error.message || t("failed")); }
      });
      buttons.append(button);
    }
    item.append(buttons);
  }
  messages.append(item);
}
function renderRecords(prepending = false) {
  const oldHeight = messages.scrollHeight;
  const oldTop = messages.scrollTop;
  const followBottom = !prepending && oldHeight - oldTop - messages.clientHeight < 60;
  messages.replaceChildren();
  const records = [...messageRecords.values()].sort((a, b) =>
    a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  if (!records.length) renderMessage("assistant", t("welcome"));
  else for (const record of records) renderMessage(record.role, record.content, record.sources, record.id);
  $("#load-earlier").hidden = !hasEarlierMessages;
  if (prepending) messages.scrollTop = oldTop + messages.scrollHeight - oldHeight;
  else messages.scrollTop = followBottom ? messages.scrollHeight : oldTop;
}
function resetMessages() {
  messageRecords = new Map();
  hasEarlierMessages = false;
  pendingRefresh = false;
}
async function ensureSession() {
  if (!token) throw new Error(at("loginTitle"));
  const legacyToken = localStorage.getItem(LEGACY_TOKEN_KEY);
  if (legacyToken) {
    try {
      await api("/conversations/claim", { method: "POST", body: JSON.stringify({ token: legacyToken }) });
      localStorage.removeItem(LEGACY_TOKEN_KEY);
    }
    catch (error) {
      if ([404, 409].includes(error.status)) localStorage.removeItem(LEGACY_TOKEN_KEY);
    }
  }
  await api("/conversations", { method: "POST", body: JSON.stringify({ language: languageSelect.value }) });
}
async function loadMessages() {
  const sessionToken = token;
  const initial = messageRecords.size === 0;
  const latest = [...messageRecords.values()].sort((a, b) =>
    a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).at(-1);
  let cursor = latest?.id;
  const records = [];
  let page;
  do {
    page = await api(cursor ? `/messages?after=${encodeURIComponent(cursor)}` : "/messages");
    if (token !== sessionToken) return;
    records.push(...page);
    cursor = page.at(-1)?.id;
  } while (page.length === MESSAGE_PAGE_SIZE && !initial);
  const feedback = await api("/feedback");
  if (token !== sessionToken) return;
  feedbackByMessage = new Map(feedback.map((item) => [item.message_id, item.rating]));
  if (initial) hasEarlierMessages = records.length === MESSAGE_PAGE_SIZE;
  for (const record of records) messageRecords.set(record.id, record);
  renderRecords();
}
$("#load-earlier").addEventListener("click", async () => {
  const oldest = [...messageRecords.values()].sort((a, b) =>
    a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))[0];
  if (!oldest) return;
  const sessionToken = token;
  const button = $("#load-earlier");
  button.disabled = true;
  try {
    const records = await api(`/messages?before=${encodeURIComponent(oldest.id)}`);
    if (token !== sessionToken) return;
    hasEarlierMessages = records.length === MESSAGE_PAGE_SIZE;
    for (const record of records) messageRecords.set(record.id, record);
    renderRecords(true);
  } catch (error) { toast(error.message || t("failed")); }
  finally { button.disabled = false; }
});
async function loadTickets() {
  const sessionToken = token;
  const tickets = await api("/tickets");
  if (token !== sessionToken) return;
  ticketList.replaceChildren();
  const active = tickets.some((ticket) => ticket.handoff_reason && ["open", "investigating"].includes(ticket.status));
  $("#handoff-status").hidden = !active;
  $("#handoff-status").textContent = active ? tx("active") : "";
  if (!tickets.length) { const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = t("noReports"); ticketList.append(empty); return; }
  for (const ticket of tickets) {
    const item = document.createElement("div"); item.className = "ticket-item";
    const title = document.createElement("strong"); title.textContent = ticket.summary;
    const meta = document.createElement("small"); meta.textContent = `${ticket.category.toUpperCase()} · ${ticket.status.toUpperCase()} · ${new Date(ticket.created_at).toLocaleDateString()}`;
    item.append(title, meta); ticketList.append(item);
  }
}
async function send(text) {
  if (busy) return;
  const sessionToken = token;
  busy = true; $("#send-button").disabled = true;
  renderMessage("player", text);
  messages.scrollTop = messages.scrollHeight;
  try {
    const result = await api("/messages", { method: "POST", body: JSON.stringify({ content: text, language: languageSelect.value }) });
    if (token !== sessionToken) return;
    renderMessage(result.message.role, result.message.content, result.message.sources, result.message.id);
    messages.scrollTop = messages.scrollHeight;
    if (result.ticket_id) { toast(t("autoTicket")); await loadTickets(); }
  } catch (error) {
    if (token !== sessionToken) return;
    toast(error.message || t("failed")); await loadMessages().catch(() => {});
  }
  finally {
    if (token !== sessionToken) return;
    busy = false;
    $("#send-button").disabled = false;
    if (pendingRefresh) {
      pendingRefresh = false;
      await Promise.all([loadMessages(), loadTickets()]).catch(() => {});
    }
  }
}
$("#chat-form").addEventListener("submit", async (event) => { event.preventDefault(); const input = $("#message-input"); const value = input.value.trim(); if (!value) return; input.value = ""; await send(value); });
$("#message-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#chat-form").requestSubmit(); } });
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => send(button.dataset.prompt)));
$("#show-report").addEventListener("click", () => $("#report-dialog").showModal());
$("#close-report").addEventListener("click", () => $("#report-dialog").close());
$("#report-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = { category: $("#report-category").value, summary: $("#report-summary").value.trim(), game_version: $("#report-version").value.trim() || null, device_info: $("#report-device").value.trim() || null };
  try { await api("/tickets", { method: "POST", body: JSON.stringify(payload) }); $("#report-dialog").close(); $("#report-form").reset(); toast(t("sent")); await loadTickets(); }
  catch (error) { toast(error.message || t("failed")); }
});
$("#refresh-tickets").addEventListener("click", () => Promise.all([loadTickets(), loadMessages()]).catch((error) => toast(error.message)));
$("#delete-data").addEventListener("click", async () => {
  if (!confirm(t("deleteConfirm"))) return;
  try { await api("/conversations/current", { method: "DELETE" }); eventController?.abort(); localStorage.removeItem(LEGACY_TOKEN_KEY); resetMessages(); await ensureSession(); await Promise.all([loadMessages(), loadTickets()]); watchEvents(); toast(t("deleted")); }
  catch (error) { toast(error.message || t("failed")); }
});
languageSelect.value = localStorage.getItem(LANG_KEY) || "en";
languageSelect.addEventListener("change", () => { localStorage.setItem(LANG_KEY, languageSelect.value); applyLanguage(); loadMessages().catch(() => {}); loadTickets().catch(() => {}); });
applyLanguage();
$("#auth-switch").addEventListener("click", () => { authMode = authMode === "login" ? "register" : "login"; renderAuth(); });
$("#player-auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#auth-submit");
  button.disabled = true;
  const email = $("#player-email").value.trim();
  const password = $("#player-password").value;
  try {
    let accessToken;
    if (authMode === "register") {
      const response = await fetch("/api/v1/auth/register", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, username: $("#player-name").value.trim() || null })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.errors?.[0]?.message || t("failed"));
      accessToken = data.token.access_token;
    } else {
      const body = new URLSearchParams({ email, password, grant_type: "password" });
      const response = await fetch("/api/v1/auth/login", { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || t("failed"));
      accessToken = data.access_token;
    }
    token = accessToken;
    localStorage.setItem(TOKEN_KEY, token);
    resetMessages();
    await ensureSession();
    await Promise.all([loadMessages(), loadTickets()]);
    showSupport();
    watchEvents();
    $("#player-password").value = "";
  } catch (error) { toast(error.message || t("failed")); }
  finally { button.disabled = false; }
});
$("#player-logout").addEventListener("click", () => {
  eventController?.abort();
  token = null;
  busy = false;
  $("#send-button").disabled = false;
  localStorage.removeItem(TOKEN_KEY);
  resetMessages();
  feedbackByMessage = new Map();
  messages.replaceChildren();
  ticketList.replaceChildren();
  showAuth();
});
async function watchEvents() {
  const sessionToken = token;
  if (!sessionToken) return;
  eventController = new AbortController();
  try {
    const response = await fetch(`${API}/events`, { headers: { Authorization: `Bearer ${sessionToken}` }, signal: eventController.signal });
    if (!response.ok || !response.body) throw new Error("event stream unavailable");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const frames = pending.split("\n\n"); pending = frames.pop();
      if (token === sessionToken && frames.some((frame) => frame.startsWith("event: refresh"))) {
        if (busy) pendingRefresh = true;
        else await Promise.all([loadMessages(), loadTickets()]);
      }
    }
  } catch (_) { /* The interval below keeps the page usable while reconnecting. */ }
  if (token === sessionToken && !eventController.signal.aborted) setTimeout(watchEvents, 3000);
}
(async () => {
  if (!token) { showAuth(); return; }
  try { await ensureSession(); await Promise.all([loadMessages(), loadTickets()]); showSupport(); watchEvents(); }
  catch (error) { token = null; localStorage.removeItem(TOKEN_KEY); resetMessages(); showAuth(); toast(error.message || t("sessionLost")); }
})();
setInterval(() => { if (token && !document.hidden) Promise.all([loadTickets(), loadMessages()]).catch(() => {}); }, 30000);
