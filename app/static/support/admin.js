const API = "/api/v1/support";
const MAX_DOCUMENT_BYTES = 50 * 1024 * 1024;
const LANGUAGE_KEY = "dots_support_language";
const TOKEN_KEY = "dots_support_admin_token";
const $ = (selector) => document.querySelector(selector);
const languageSelect = $("#admin-language");
const dateLocales = { en: "en-US", es: "es-ES", zh: "zh-CN" };
let adminToken = sessionStorage.getItem(TOKEN_KEY);
let dashboardData = null;
let eventController = null;
let selectedTicketId = null;
const ticketDrafts = new Map();
const extraStrings = {
  en: { claim: "Claim", release: "Release", assigned: "Assigned to", unassigned: "Unassigned", overdue: "Review overdue", quality: "Quality signals", positive: "Helpful", negative: "Needs work", staleArticles: "Articles due for review", staleDocuments: "Documents due for review", waiting: "Unassigned reports", uploadImage: "Attach image", imagePage: "Document page (optional)", imageDescription: "Reviewed image description", generateDescription: "Suggest with vision model", saveDescription: "Save description", imageUploaded: "Image uploaded. Review its description before publishing.", imageSaved: "Image description saved. Publish the document to make it visible.", imageDeleted: "Image deleted.", removeImage: "Delete image", imageLimit: "Image exceeds 10 MiB.", imageDraft: "Vision suggestion is a draft. Check visible details before saving." },
  es: { claim: "Asignar a mí", release: "Liberar", assigned: "Asignado a", unassigned: "Sin asignar", overdue: "Revisión vencida", quality: "Indicadores de calidad", positive: "Útil", negative: "Necesita mejorar", staleArticles: "Artículos para revisar", staleDocuments: "Documentos para revisar", waiting: "Informes sin asignar", uploadImage: "Adjuntar imagen", imagePage: "Página del documento (opcional)", imageDescription: "Descripción revisada", generateDescription: "Sugerir con modelo visual", saveDescription: "Guardar descripción", imageUploaded: "Imagen subida. Revisa su descripción antes de publicar.", imageSaved: "Descripción guardada. Publica el documento para mostrarla.", imageDeleted: "Imagen eliminada.", removeImage: "Eliminar imagen", imageLimit: "La imagen supera 10 MiB.", imageDraft: "La sugerencia visual es un borrador. Comprueba los detalles antes de guardar." },
  zh: { claim: "领取工单", release: "释放工单", assigned: "处理员工", unassigned: "待领取", overdue: "知识复审已到期", quality: "质量指标", positive: "有帮助", negative: "没帮助", staleArticles: "待复审条目", staleDocuments: "待复审文档", waiting: "待领取工单", uploadImage: "添加知识库图片", imagePage: "对应文档页码（可选）", imageDescription: "审核后的图片描述", generateDescription: "视觉模型生成描述草稿", saveDescription: "保存描述", imageUploaded: "图片已上传。请审核描述后再发布。", imageSaved: "图片描述已保存。发布文档后才会向玩家展示。", imageDeleted: "图片已删除。", removeImage: "删除图片", imageLimit: "图片不能超过 10 MiB。", imageDraft: "模型生成的是草稿；请核对可见内容再保存。" }
};
const progressStrings = {
  en: { uploading: "Uploading file", queued: "Queued for processing", parsing: "Parsing text and OCR", splitting: "Preparing search passages", embedding: "Creating vector index", saving: "Saving document", completed: "Index ready for review", failed: "Processing failed", overall: "Overall" },
  es: { uploading: "Subiendo archivo", queued: "En cola para procesar", parsing: "Extrayendo texto y OCR", splitting: "Preparando fragmentos", embedding: "Creando índice vectorial", saving: "Guardando documento", completed: "Índice listo para revisión", failed: "Error de procesamiento", overall: "Total" },
  zh: { uploading: "正在上传文件", queued: "已排队，等待处理", parsing: "正在解析文字和 OCR 识别", splitting: "正在切分检索片段", embedding: "正在生成向量索引", saving: "正在保存文档", completed: "索引已完成，等待审核", failed: "处理失败", overall: "总进度" }
};
function currentStaffId() {
  try { return String(JSON.parse(atob(adminToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).sub); }
  catch (_) { return null; }
}

const strings = {
  en: {
    staffOnly: "STAFF ONLY", desk: "Support desk", language: "Language", playerView: "Player view ↗",
    secureAccess: "SECURE ACCESS", signInTitle: "Sign in to support", signInHint: "Use a registered staff account to review player reports and approved answers.",
    email: "Email", password: "Password", signIn: "Sign in", missionOperations: "MISSION OPERATIONS",
    overview: "Player support overview", signOut: "Sign out", inbox: "LIVE INBOX", playerReports: "Player conversations",
    refresh: "Refresh", approvedAnswers: "APPROVED ANSWERS", knowledge: "Knowledge", newArticle: "Create text source",
    knowledgeReview: "DOCUMENT KNOWLEDGE", close: "Close", title: "Title", verifiedFacts: "Source text / verified facts",
    sourceUrl: "Source URL", draftHint: "Saving creates a draft. Publish it after checking the source.", saveDraft: "Save draft",
    reviseArticle: "Revise article", openReports: "Open reports", allReports: "All reports",
    publishedArticles: "Published articles", conversations: "Conversations / 30d", noReports: "No player reports yet.",
    noKnowledge: "No knowledge articles yet. Add a sourced draft.", unknown: "Unknown", version: "Version", device: "Device",
    replyPlaceholder: "Write a reply to the player…", saveReply: "Send reply / update status",
    reportUpdated: "Report updated.", viewSource: "View source ↗", editDraft: "Edit draft", publish: "Publish",
    articlePublished: "Article published.", draftSaved: "Draft saved. Review it before publishing.",
    signInFailed: "Sign in failed", requestFailed: "Request failed", connectionFailed: "Could not connect to the server.",
    badCredentials: "Incorrect email or password.", staffRequired: "This account is not enabled for staff access.",
    open: "Open", investigating: "Investigating", resolved: "Resolved", closed: "Closed",
    selectConversation: "Select a conversation", emptyInbox: "No player conversations yet.", playerRole: "PLAYER", assistantRole: "AI SUPPORT", staffRole: "SUPPORT STAFF", handoffRole: "HANDOFF", assignedTo: "Assigned to", waitingClaim: "Claim this conversation to reply.",
    draft: "Draft", published: "Published", bug: "Bug", achievement: "Achievement", save: "Save / cloud sync",
    performance: "Performance", other: "Other", indexedSources: "INDEXED SOURCES", documents: "Documents",
    chooseFile: "Choose TXT, MD, PDF, DOCX or image (max 50 MiB)", sourceUrlOptional: "Source URL (optional)",
    fileTooLarge: "File exceeds the 50 MiB upload limit.",
    uploadDocument: "Upload and index draft", documentUploaded: "Document indexed as draft.",
    noDocuments: "No indexed documents yet.", chunks: "passages", preview: "Review text", reindex: "Reindex",
    remove: "Delete", removeConfirm: "Delete this document and its index?", context: "Full conversation",
    handoff: "Human handoff", documentPublished: "Document published.", documentRemoved: "Document deleted.",
    replaceFile: "Replace file", documentReplaced: "Document replaced; review the new draft.",
    sourceType: "Source type", officialSource: "Official", communitySource: "Community", internalSource: "Internal",
    unknownSource: "Unverified", gameVersion: "Game version", documentLanguage: "Document language",
    sourceKeywords: "Known terms / aliases (comma separated)", reviewPassages: "Review passages",
    sourceContent: "Extracted source text", passage: "Passage", page: "Page", keywords: "Suggested search terms / aliases (review and edit)",
    saveKeywords: "Save terms", keywordsSaved: "Search terms saved.", noKeywords: "No terms yet"
  },
  es: {
    staffOnly: "SOLO PERSONAL", desk: "Panel de soporte", language: "Idioma", playerView: "Vista de jugador ↗",
    secureAccess: "ACCESO SEGURO", signInTitle: "Acceder al soporte", signInHint: "Usa una cuenta autorizada para revisar informes y respuestas aprobadas.",
    email: "Correo", password: "Contraseña", signIn: "Entrar", missionOperations: "OPERACIONES",
    overview: "Resumen de soporte", signOut: "Cerrar sesión", inbox: "MENSAJES EN VIVO", playerReports: "Conversaciones",
    refresh: "Actualizar", approvedAnswers: "RESPUESTAS APROBADAS", knowledge: "Conocimiento", newArticle: "Crear texto",
    knowledgeReview: "DOCUMENTOS", close: "Cerrar", title: "Título", verifiedFacts: "Texto / datos verificados",
    sourceUrl: "URL de la fuente", draftHint: "Se guardará como borrador. Revisa la fuente antes de publicarlo.", saveDraft: "Guardar borrador",
    reviseArticle: "Editar artículo", openReports: "Informes abiertos", allReports: "Todos los informes",
    publishedArticles: "Artículos publicados", conversations: "Conversaciones / 30 días", noReports: "Aún no hay informes.",
    noKnowledge: "Aún no hay artículos. Añade un borrador con su fuente.", unknown: "Desconocido", version: "Versión", device: "Dispositivo",
    replyPlaceholder: "Escribe una respuesta al jugador…", saveReply: "Enviar respuesta / actualizar estado",
    reportUpdated: "Informe actualizado.", viewSource: "Ver fuente ↗", editDraft: "Editar borrador", publish: "Publicar",
    articlePublished: "Artículo publicado.", draftSaved: "Borrador guardado. Revísalo antes de publicarlo.",
    signInFailed: "No se pudo iniciar sesión", requestFailed: "Error de solicitud", connectionFailed: "No se pudo conectar con el servidor.",
    badCredentials: "Correo o contraseña incorrectos.", staffRequired: "Esta cuenta no tiene acceso de personal.",
    open: "Abierto", investigating: "Investigando", resolved: "Resuelto", closed: "Cerrado",
    selectConversation: "Selecciona una conversación", emptyInbox: "Aún no hay conversaciones.", playerRole: "JUGADOR", assistantRole: "ASISTENCIA IA", staffRole: "SOPORTE", handoffRole: "TRANSFERENCIA", assignedTo: "Asignado a", waitingClaim: "Toma la conversación para responder.",
    draft: "Borrador", published: "Publicado", bug: "Error", achievement: "Logro", save: "Guardado / nube",
    performance: "Rendimiento", other: "Otro", indexedSources: "FUENTES INDEXADAS", documents: "Documentos",
    chooseFile: "Selecciona TXT, MD, PDF, DOCX o imagen (máx. 50 MiB)", sourceUrlOptional: "URL de origen (opcional)",
    fileTooLarge: "El archivo supera el límite de 50 MiB.",
    uploadDocument: "Subir e indexar borrador", documentUploaded: "Documento indexado como borrador.",
    noDocuments: "Aún no hay documentos.", chunks: "fragmentos", preview: "Revisar texto", reindex: "Reindexar",
    remove: "Eliminar", removeConfirm: "¿Eliminar el documento y su índice?", context: "Conversación completa",
    handoff: "Derivado a una persona", documentPublished: "Documento publicado.", documentRemoved: "Documento eliminado.",
    replaceFile: "Sustituir archivo", documentReplaced: "Documento sustituido; revisa el borrador.",
    sourceType: "Tipo de fuente", officialSource: "Oficial", communitySource: "Comunidad", internalSource: "Interna",
    unknownSource: "Sin verificar", gameVersion: "Versión del juego", documentLanguage: "Idioma del documento",
    sourceKeywords: "Términos / alias conocidos (separados por coma)", reviewPassages: "Revisar fragmentos",
    sourceContent: "Texto extraído", passage: "Fragmento", page: "Página", keywords: "Términos / alias sugeridos (revisa y edita)",
    saveKeywords: "Guardar términos", keywordsSaved: "Términos guardados.", noKeywords: "Aún no hay términos"
  },
  zh: {
    staffOnly: "仅限客服人员", desk: "客服工作台", language: "语言", playerView: "玩家页面 ↗",
    secureAccess: "员工登录", signInTitle: "登录客服工作台", signInHint: "使用已授权的员工账号处理玩家报告、审核知识内容。",
    email: "邮箱", password: "密码", signIn: "登录", missionOperations: "任务控制台",
    overview: "玩家支持概览", signOut: "退出登录", inbox: "实时消息", playerReports: "玩家会话",
    refresh: "刷新", approvedAnswers: "已审核内容", knowledge: "知识库", newArticle: "新建正文",
    knowledgeReview: "文档知识库", close: "关闭", title: "标题", verifiedFacts: "正文 / 已核实信息",
    sourceUrl: "来源链接", draftHint: "保存后为草稿。核对来源后再发布。", saveDraft: "保存草稿",
    reviseArticle: "编辑条目", openReports: "未关闭报告", allReports: "报告总数",
    publishedArticles: "已发布条目", conversations: "近 30 天会话", noReports: "暂无玩家报告。",
    noKnowledge: "暂无知识条目。请先添加附有来源的草稿。", unknown: "未知", version: "版本", device: "设备",
    replyPlaceholder: "在这里回复玩家…", saveReply: "发送回复并更新状态",
    reportUpdated: "报告已更新。", viewSource: "查看来源 ↗", editDraft: "编辑草稿", publish: "发布",
    articlePublished: "条目已发布。", draftSaved: "草稿已保存，请核对后发布。",
    signInFailed: "登录失败", requestFailed: "请求失败", connectionFailed: "无法连接服务器。",
    badCredentials: "邮箱或密码不正确。", staffRequired: "此账号未获得客服权限。",
    open: "待处理", investigating: "调查中", resolved: "已解决", closed: "已关闭",
    selectConversation: "选择一条会话", emptyInbox: "暂时没有玩家会话。", playerRole: "玩家", assistantRole: "AI 客服", staffRole: "人工客服", handoffRole: "转人工", assignedTo: "当前客服", waitingClaim: "领取会话后即可回复玩家。",
    draft: "草稿", published: "已发布", bug: "故障", achievement: "成就", save: "存档 / 云同步",
    performance: "性能", other: "其他", indexedSources: "向量化资料", documents: "文档知识库",
    chooseFile: "选择 TXT、MD、PDF、DOCX 或图片（最大 50 MiB）", sourceUrlOptional: "来源链接（可选）",
    fileTooLarge: "文件超过 50 MiB 上传上限。",
    uploadDocument: "上传并建立草稿索引", documentUploaded: "文档已建立索引，待审核发布。",
    noDocuments: "暂无已索引文档。", chunks: "个片段", preview: "审核正文", reindex: "重建索引",
    remove: "删除", removeConfirm: "删除这份文档及其向量索引？", context: "完整对话",
    handoff: "人工转接", documentPublished: "文档已发布。", documentRemoved: "文档已删除。",
    replaceFile: "替换文件", documentReplaced: "文件已替换，请审核新草稿。",
    sourceType: "资料来源", officialSource: "官方", communitySource: "社区", internalSource: "内部",
    unknownSource: "未核实", gameVersion: "适用游戏版本", documentLanguage: "文档语言",
    sourceKeywords: "已知术语 / 别名（逗号分隔）", reviewPassages: "审核切块",
    sourceContent: "解析后的原文", passage: "切块", page: "页码", keywords: "候选检索词 / 别名（审核后可修改）",
    saveKeywords: "保存关键词", keywordsSaved: "关键词已保存。", noKeywords: "暂无关键词"
  }
};

function t(key) { return (strings[languageSelect.value] || strings.en)[key] || extraStrings[languageSelect.value]?.[key] || strings.en[key] || extraStrings.en[key] || key; }
function el(tag, value = "", className = "") {
  const item = document.createElement(tag);
  item.textContent = value;
  if (className) item.className = className;
  return item;
}
function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.remove("visible"), 5000);
}
function formatDate(value) { return new Date(value).toLocaleString(dateLocales[languageSelect.value] || dateLocales.en); }
function errorText(detail, fallback) {
  if (detail === "Incorrect email or password") return t("badCredentials");
  if (detail === "Support staff access required") return t("staffRequired");
  return typeof detail === "string" ? detail : fallback;
}
function createUploadProgress(container) {
  const root = el("div", "", "upload-progress");
  root.hidden = true;
  root.setAttribute("role", "status");
  root.setAttribute("aria-live", "polite");
  const label = el("span", "", "upload-progress-label");
  const percent = el("span", "", "upload-progress-percent");
  const bar = document.createElement("progress");
  bar.max = 100;
  bar.value = 0;
  root.append(label, percent, bar);
  container.append(root);
  return { root, label, percent, bar };
}
function setUploadProgress(widget, phase, value) {
  const percent = Math.max(0, Math.min(100, Math.round(value)));
  widget.root.hidden = false;
  widget.root.classList.toggle("failed", phase === "failed");
  widget.root.classList.toggle("working", !["failed", "completed", "uploading"].includes(phase));
  widget.label.textContent = progressStrings[languageSelect.value]?.[phase] || progressStrings.en[phase] || phase;
  widget.percent.textContent = `${progressStrings[languageSelect.value]?.overall || progressStrings.en.overall} ${percent}%`;
  widget.bar.value = percent;
}
function uploadDocument(path, method, form, widget) {
  return new Promise((resolve, reject) => {
    const uploadId = crypto.randomUUID();
    form.append("upload_id", uploadId);
    const xhr = new XMLHttpRequest();
    let timer = null;
    let active = true;
    let polling = false;
    let pollErrors = 0;
    const deadline = setTimeout(() => { stop(); reject(new Error(t("requestFailed"))); }, 15 * 60 * 1000);
    const stop = () => { active = false; if (timer) clearInterval(timer); clearTimeout(deadline); };
    const poll = async () => {
      if (!active || polling) return;
      polling = true;
      try {
        const state = await api(`/admin/document-uploads/${uploadId}`);
        if (!active) return;
        pollErrors = 0;
        setUploadProgress(widget, state.phase, state.percent);
        if (state.status === "completed") { stop(); resolve(state); }
        if (state.status === "failed") { stop(); reject(new Error(t("requestFailed"))); }
      } catch (error) {
        if (++pollErrors >= 10) { stop(); reject(error); }
      }
      finally { polling = false; }
    };
    setUploadProgress(widget, "uploading", 0);
    xhr.open(method, API + path);
    xhr.setRequestHeader("Authorization", `Bearer ${adminToken}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        setUploadProgress(widget, "uploading", 30 * event.loaded / event.total);
        widget.label.textContent += ` ${Math.round(100 * event.loaded / event.total)}%`;
      }
    };
    xhr.upload.onload = () => {
      setUploadProgress(widget, "queued", 30);
    };
    xhr.onload = () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText); } catch (_) { /* Empty error response. */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        setUploadProgress(widget, payload.phase || "queued", payload.percent || 30);
        timer = setInterval(poll, 1000);
        poll();
      } else {
        stop();
        setUploadProgress(widget, "failed", widget.bar.value);
        reject(new Error(errorText(payload.detail, `${t("requestFailed")} (${xhr.status})`)));
      }
    };
    xhr.onerror = () => { stop(); setUploadProgress(widget, "failed", widget.bar.value); reject(new Error(t("connectionFailed"))); };
    xhr.send(form);
  });
}
async function waitForIndex(uploadId, widget) {
  for (let attempt = 0; attempt < 900; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const state = await api(`/admin/document-uploads/${uploadId}`);
    setUploadProgress(widget, state.phase, state.percent);
    if (state.status === "completed") return state;
    if (state.status === "failed") throw new Error(t("requestFailed"));
  }
  throw new Error(t("requestFailed"));
}
async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(API + path, {
      ...options,
      headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), Authorization: `Bearer ${adminToken}`, ...(options.headers || {}) }
    });
  } catch (_) { throw new Error(t("connectionFailed")); }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(errorText(payload.detail, `${t("requestFailed")} (${response.status})`));
  }
  return response.status === 204 ? null : response.json();
}
function showDashboard() { $("#login-panel").hidden = true; $("#dashboard").hidden = false; }
function showLogin() { $("#login-panel").hidden = false; $("#dashboard").hidden = true; }
function applyLanguage() {
  document.documentElement.lang = languageSelect.value;
  document.title = `DOTS Survivors · ${t("desk")}`;
  document.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => { node.setAttribute("aria-label", t(node.dataset.i18nAria)); });
  if (dashboardData) renderDashboard();
  if ($("#knowledge-dialog").open) $("#knowledge-form-title").textContent = t("newArticle");
}
function renderDashboard() {
  const { stats, tickets, documents, quality } = dashboardData;
  const statsBox = $("#stats");
  statsBox.replaceChildren();
  for (const [label, value] of [
    ["openReports", stats.open_tickets], ["allReports", stats.total_tickets],
    ["documents", stats.published_documents], ["conversations", stats.conversations_30d]
  ]) {
    const card = el("div", "", "stat");
    card.append(el("strong", String(value)), el("span", t(label)));
    statsBox.append(card);
  }
  let qualityBox = $("#quality");
  if (!qualityBox) { qualityBox = el("div", "", "quality-strip"); qualityBox.id = "quality"; statsBox.after(qualityBox); }
  qualityBox.replaceChildren(el("strong", `${t("quality")}: `));
  for (const [label, value] of [["positive", quality.positive_feedback], ["negative", quality.negative_feedback], ["staleDocuments", quality.stale_documents], ["waiting", quality.unassigned_tickets]]) {
    qualityBox.append(el("span", `${t(label)} ${value}`));
  }
  renderTickets(tickets);
  renderDocuments(documents);
}
async function loadDashboard() {
  const [stats, tickets, documents, quality] = await Promise.all([
    api("/admin/overview"), api("/admin/tickets"), api("/admin/documents"), api("/admin/quality")
  ]);
  dashboardData = { stats, tickets, documents, quality };
  showDashboard();
  renderDashboard();
}
function renderTickets(tickets) {
  const list = $("#admin-tickets");
  const currentReply = list.querySelector(".inbox-composer textarea");
  const currentStatus = list.querySelector(".inbox-composer select");
  if (selectedTicketId && currentReply && currentStatus && !currentReply.disabled) {
    if (currentReply.value || currentStatus.value !== currentReply.dataset.savedStatus) {
      ticketDrafts.set(selectedTicketId, { status: currentStatus.value, reply: currentReply.value });
    } else ticketDrafts.delete(selectedTicketId);
  }
  list.className = "support-inbox";
  list.replaceChildren();
  if (!tickets.length) { list.append(el("p", t("emptyInbox"), "empty inbox-empty")); return; }
  const selected = tickets.find((ticket) => ticket.id === selectedTicketId)
    || tickets.find((ticket) => ["open", "investigating"].includes(ticket.status) && !ticket.assigned_to)
    || tickets[0];
  selectedTicketId = selected.id;
  const queue = el("nav", "", "inbox-threads");
  queue.setAttribute("aria-label", t("playerReports"));
  for (const ticket of tickets) {
    const button = el("button", "", "inbox-thread");
    button.type = "button";
    button.classList.toggle("selected", ticket.id === selected.id);
    button.setAttribute("aria-current", ticket.id === selected.id ? "true" : "false");
    const heading = el("span", "", "inbox-thread-heading");
    heading.append(el("strong", ticket.summary), el("span", t(ticket.status), `inbox-status status-${ticket.status}`));
    button.append(heading);
    button.append(el("small", `${t(ticket.category)} · ${formatDate(ticket.updated_at)}`));
    button.append(el("small", ticket.assigned_to ? `${t("assignedTo")} #${ticket.assigned_to}` : t("unassigned")));
    if (ticket.handoff_reason) button.append(el("span", `${t("handoffRole")} · ${ticket.handoff_reason}`, "inbox-handoff-tag"));
    button.addEventListener("click", () => {
      selectedTicketId = ticket.id;
      renderTickets(tickets);
    });
    queue.append(button);
  }
  const conversation = el("section", "", "inbox-conversation");
  conversation.setAttribute("aria-label", t("selectConversation"));
  list.append(queue, conversation);
  renderTicketConversation(selected, conversation);
}
async function renderTicketConversation(ticket, panel) {
  const owner = ticket.assigned_to ? `${t("assignedTo")} #${ticket.assigned_to}` : t("unassigned");
  const header = el("header", "", "inbox-conversation-head");
  const title = el("div", "", "inbox-conversation-title");
  title.append(el("h4", ticket.summary), el("small", `${t(ticket.category)} · ${owner} · ${formatDate(ticket.created_at)}`));
  header.append(title);
  const canClaim = ["open", "investigating"].includes(ticket.status)
    && (!ticket.assigned_to || String(ticket.assigned_to) === currentStaffId());
  if (canClaim) {
    const assigned = String(ticket.assigned_to) === currentStaffId();
    const claim = el("button", t(assigned ? "release" : "claim"), "inbox-claim-button");
    claim.type = "button";
    claim.addEventListener("click", async () => {
      try {
        await api(`/admin/tickets/${ticket.id}/${assigned ? "release" : "claim"}`, { method: "POST" });
        await loadDashboard();
      } catch (error) { toast(error.message); }
    });
    header.append(claim);
  }
  panel.replaceChildren(header);
  if (ticket.game_version || ticket.device_info) {
    panel.append(el("div", `${t("version")}: ${ticket.game_version || t("unknown")} · ${t("device")}: ${ticket.device_info || t("unknown")}`, "inbox-context-line"));
  }
  const transcript = el("div", "", "inbox-transcript");
  transcript.append(el("p", t("selectConversation"), "empty"));
  panel.append(transcript);
  const composer = el("form", "", "inbox-composer");
  const status = document.createElement("select");
  status.setAttribute("aria-label", t("saveReply"));
  for (const value of ["open", "investigating", "resolved", "closed"]) {
    const option = el("option", t(value));
    option.value = value;
    status.append(option);
  }
  status.value = ticketDrafts.get(ticket.id)?.status || ticket.status;
  const reply = document.createElement("textarea");
  reply.placeholder = String(ticket.assigned_to) === currentStaffId() ? t("replyPlaceholder") : t("waitingClaim");
  reply.maxLength = 4000;
  reply.value = ticketDrafts.get(ticket.id)?.reply || "";
  reply.dataset.savedStatus = ticket.status;
  const submit = el("button", t("saveReply"), "inbox-send-button");
  submit.type = "submit";
  const canReply = String(ticket.assigned_to) === currentStaffId();
  status.disabled = !canReply;
  reply.disabled = !canReply;
  submit.disabled = !canReply;
  if (!canReply) ticketDrafts.delete(ticket.id);
  composer.append(status, reply, submit);
  composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = reply.value.trim();
    if (!message && status.value === ticket.status) return;
    submit.disabled = true;
    try {
      await api(`/admin/tickets/${ticket.id}`, {
        method: "PATCH", body: JSON.stringify({ status: status.value, reply: message || null })
      });
      ticketDrafts.delete(ticket.id);
      reply.value = "";
      toast(t("reportUpdated"));
      await loadDashboard();
    } catch (error) { toast(error.message); }
    finally { submit.disabled = false; }
  });
  panel.append(composer);
  try {
    const data = await api(`/admin/tickets/${ticket.id}/context`);
    if (!panel.isConnected || selectedTicketId !== ticket.id) return;
    transcript.replaceChildren();
    for (const message of data.messages) {
      const role = message.role === "player" ? "playerRole"
        : message.role === "assistant" ? "assistantRole"
          : message.role === "handoff" ? "handoffRole" : "staffRole";
      const bubble = el("article", "", `inbox-message role-${message.role}`);
      bubble.append(el("small", t(role)), el("p", message.content), el("time", formatDate(message.created_at)));
      if (message.sources.length) {
        const sources = el("div", "", "inbox-message-sources");
        for (const source of message.sources) {
          const link = el("a", source.title);
          link.href = source.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          sources.append(link);
          if (source.image_url) {
            const image = document.createElement("img");
            image.src = source.image_url;
            image.alt = source.image_alt || source.title;
            image.loading = "lazy";
            sources.append(image);
          }
        }
        bubble.append(sources);
      }
      transcript.append(bubble);
    }
    transcript.scrollTop = transcript.scrollHeight;
  } catch (error) { toast(error.message); }
}
function renderDocuments(documents) {
  const list = $("#admin-documents");
  list.replaceChildren();
  if (!documents.length) { list.append(el("p", t("noDocuments"), "empty")); return; }
  for (const document of documents) {
    const item = el("article", "", "admin-item");
    item.append(el("h4", document.title), el("small", `${document.filename} · ${document.chunk_count} ${t("chunks")} · ${t(document.status)}`));
    const meta = [t(document.source_type === "official" ? "officialSource" : document.source_type === "community" ? "communitySource" : document.source_type === "internal" ? "internalSource" : "unknownSource"), document.language?.toUpperCase(), document.game_version ? `v${document.game_version}` : null].filter(Boolean);
    item.append(el("small", meta.join(" · "), "document-meta-line"));
    if (document.review_due_at && new Date(document.review_due_at) <= new Date()) item.append(el("span", t("overdue"), "overdue-badge"));
    const actions = el("div", "", "admin-item-actions");
    const preview = el("button", t("reviewPassages"));
    preview.addEventListener("click", async () => {
      try {
        const source = await api(`/admin/documents/${document.id}`);
        item.querySelector(".document-review")?.remove();
        const review = el("div", "", "document-review");
        if (source.source_url) {
          const sourceLink = el("a", source.source_url);
          sourceLink.href = source.source_url; sourceLink.target = "_blank"; sourceLink.rel = "noopener noreferrer";
          sourceLink.className = "document-source-link"; review.append(sourceLink);
        }
        const original = window.document.createElement("details");
        original.className = "document-original";
        original.append(el("summary", t("sourceContent")), el("pre", source.content, "document-source-text"));
        review.append(original);
        const chunks = el("div", "", "document-chunks");
        for (const chunk of source.chunks) {
          const card = window.document.createElement("details");
          card.className = "document-chunk";
          const heading = `${t("passage")} ${chunk.position + 1}${chunk.page_number ? ` · ${t("page")} ${chunk.page_number}` : ""}`;
          card.append(el("summary", heading), el("pre", chunk.content, "document-chunk-text"));
          const keywordLabel = el("label", t("keywords"), "document-keyword-label");
          const keywordInput = window.document.createElement("input");
          keywordInput.value = (chunk.keywords || []).join(", ");
          keywordInput.maxLength = 1000;
          keywordInput.setAttribute("aria-label", t("keywords"));
          keywordLabel.append(keywordInput);
          const save = el("button", t("saveKeywords"));
          save.type = "button";
          save.addEventListener("click", async () => {
            save.disabled = true;
            try {
              const updated = await api(`/admin/documents/${document.id}/chunks/${chunk.id}/keywords`, {
                method: "PUT",
                body: JSON.stringify({ keywords: keywordInput.value.split(/[,，\n]/).map((term) => term.trim()).filter(Boolean) })
              });
              chunk.keywords = updated.keywords;
              keywordInput.value = updated.keywords.join(", ");
              toast(t("keywordsSaved"));
            } catch (error) { toast(error.message); }
            finally { save.disabled = false; }
          });
          card.append(keywordLabel, save);
          chunks.append(card);
        }
        review.append(chunks);
        await appendImageReview(review, document, source.images || []);
        item.append(review);
      } catch (error) { toast(error.message); }
    });
    actions.append(preview);
    const replacement = window.document.createElement("input");
    replacement.type = "file"; replacement.accept = ".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.webp"; replacement.hidden = true;
    let replacementProgress = null;
    replacement.addEventListener("change", async () => {
      if (!replacement.files.length) return;
      if (replacement.files[0].size > MAX_DOCUMENT_BYTES) {
        toast(t("fileTooLarge")); replacement.value = ""; return;
      }
      const form = new FormData();
      form.append("title", document.title); form.append("file", replacement.files[0]);
      if (document.source_url) form.append("source_url", document.source_url);
      form.append("source_type", document.source_type || "unknown");
      form.append("game_version", document.game_version || "");
      form.append("language", document.language || "en");
      form.append("keywords", (document.keywords || []).join(", "));
      replacementProgress ||= createUploadProgress(item);
      replace.disabled = true;
      try {
        await uploadDocument(`/admin/documents/${document.id}`, "PUT", form, replacementProgress);
        toast(t("documentReplaced")); await loadDashboard();
      } catch (error) { toast(error.message); }
      finally { replace.disabled = false; replacement.value = ""; }
    });
    const replace = el("button", t("replaceFile"));
    replace.addEventListener("click", () => replacement.click());
    actions.append(replace, replacement);
    if (document.status !== "published") {
      const publish = el("button", t("publish"));
      publish.addEventListener("click", async () => {
        try { await api(`/admin/documents/${document.id}/publish`, { method: "POST" }); toast(t("documentPublished")); await loadDashboard(); }
        catch (error) { toast(error.message); }
      });
      actions.append(publish);
    }
    const reindex = el("button", t("reindex"));
    reindex.addEventListener("click", async () => {
      reindex.disabled = true;
      const progress = createUploadProgress(item);
      try {
        const job = await api(`/admin/documents/${document.id}/reindex`, { method: "POST" });
        setUploadProgress(progress, "queued", 30);
        await waitForIndex(job.id, progress);
        await loadDashboard();
      }
      catch (error) { toast(error.message); }
      finally { reindex.disabled = false; }
    });
    const remove = el("button", t("remove"));
    remove.addEventListener("click", async () => {
      if (!confirm(t("removeConfirm"))) return;
      try { await api(`/admin/documents/${document.id}`, { method: "DELETE" }); toast(t("documentRemoved")); await loadDashboard(); }
      catch (error) { toast(error.message); }
    });
    actions.append(reindex, remove);
    item.append(actions);
    list.append(item);
  }
}
async function appendImageReview(review, document, images) {
  const section = el("div", "", "document-images");
  const heading = el("h5", `${t("uploadImage")} (${images.length})`);
  section.append(heading);
  const upload = el("form", "", "document-image-upload");
  const file = documentNode("input"); file.type = "file"; file.accept = ".png,.jpg,.jpeg,.webp"; file.required = true;
  const page = documentNode("input"); page.type = "number"; page.min = "1"; page.max = "200"; page.placeholder = t("imagePage");
  const submit = el("button", t("uploadImage")); submit.type = "submit";
  upload.append(file, page, submit);
  upload.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!file.files.length) return;
    if (file.files[0].size > 10 * 1024 * 1024) { toast(t("imageLimit")); return; }
    const form = new FormData(); form.append("file", file.files[0]);
    if (page.value) form.append("page_number", page.value);
    submit.disabled = true;
    try { await api(`/admin/documents/${document.id}/images`, { method: "POST", body: form }); toast(t("imageUploaded")); await loadDashboard(); }
    catch (error) { toast(error.message); }
    finally { submit.disabled = false; }
  });
  section.append(upload);
  for (const image of images) {
    const card = el("div", "", "document-image-card");
    card.append(el("strong", image.filename));
    const preview = documentNode("img"); preview.alt = image.description || image.filename;
    card.append(preview);
    fetch(`${API}/admin/images/${image.id}/content`, { headers: { Authorization: `Bearer ${adminToken}` } })
      .then((response) => { if (!response.ok) throw new Error(t("requestFailed")); return response.blob(); })
      .then((blob) => { const url = URL.createObjectURL(blob); preview.src = url; preview.onload = () => URL.revokeObjectURL(url); })
      .catch((error) => toast(error.message));
    const caption = documentNode("textarea"); caption.value = image.description || ""; caption.maxLength = 2000;
    const captionLabel = el("label", t("imageDescription")); captionLabel.append(caption);
    const pageInput = documentNode("input"); pageInput.type = "number"; pageInput.min = "1"; pageInput.max = "200"; pageInput.value = image.page_number || "";
    const pageLabel = el("label", t("imagePage")); pageLabel.append(pageInput);
    const actions = el("div", "", "document-image-actions");
    const generate = el("button", t("generateDescription")); generate.type = "button";
    generate.addEventListener("click", async () => {
      generate.disabled = true;
      try { const result = await api(`/admin/images/${image.id}/describe`, { method: "POST" }); caption.value = result.description; toast(t("imageDraft")); }
      catch (error) { toast(error.message); }
      finally { generate.disabled = false; }
    });
    const save = el("button", t("saveDescription")); save.type = "button";
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        await api(`/admin/images/${image.id}`, { method: "PUT", body: JSON.stringify({ description: caption.value.trim(), page_number: pageInput.value ? Number(pageInput.value) : null }) });
        toast(t("imageSaved")); await loadDashboard();
      } catch (error) { toast(error.message); }
      finally { save.disabled = false; }
    });
    const remove = el("button", t("removeImage")); remove.type = "button";
    remove.addEventListener("click", async () => {
      if (!confirm(t("removeConfirm"))) return;
      try { await api(`/admin/images/${image.id}`, { method: "DELETE" }); toast(t("imageDeleted")); await loadDashboard(); }
      catch (error) { toast(error.message); }
    });
    actions.append(generate, save, remove);
    card.append(captionLabel, pageLabel, actions);
    section.append(card);
  }
  review.append(section);
}
function documentNode(tag) { return window.document.createElement(tag); }
function openArticle() {
  $("#knowledge-form-title").textContent = t("newArticle");
  $("#knowledge-form").reset();
  $("#knowledge-dialog").showModal();
}

languageSelect.value = localStorage.getItem(LANGUAGE_KEY) || (navigator.language.startsWith("zh") ? "zh" : "en");
languageSelect.addEventListener("change", () => {
  localStorage.setItem(LANGUAGE_KEY, languageSelect.value);
  applyLanguage();
});
applyLanguage();
$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = new URLSearchParams({
    email: $("#admin-email").value,
    password: $("#admin-password").value,
    grant_type: "password"
  });
  try {
    const response = await fetch("/api/v1/auth/login", { method: "POST", body });
    const data = await response.json();
    if (!response.ok) throw new Error(errorText(data.detail, t("signInFailed")));
    adminToken = data.access_token;
    sessionStorage.setItem(TOKEN_KEY, adminToken);
    await loadDashboard();
    watchEvents();
    $("#admin-password").value = "";
  } catch (error) {
    adminToken = null;
    sessionStorage.removeItem(TOKEN_KEY);
    toast(error.message || t("connectionFailed"));
  }
});
$("#logout").addEventListener("click", () => {
  eventController?.abort();
  adminToken = null;
  dashboardData = null;
  ticketDrafts.clear();
  sessionStorage.removeItem(TOKEN_KEY);
  showLogin();
});
$("#refresh-admin").addEventListener("click", () => loadDashboard().catch((error) => toast(error.message)));
$("#new-article").addEventListener("click", () => openArticle());
const documentUploadProgress = createUploadProgress($("#document-form"));
const textUploadProgress = createUploadProgress($("#knowledge-form"));
$("#document-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const selectedFile = $("#document-file").files[0];
  if (selectedFile.size > MAX_DOCUMENT_BYTES) { toast(t("fileTooLarge")); return; }
  const form = new FormData();
  form.append("title", $("#document-title").value.trim());
  form.append("file", selectedFile);
  if ($("#document-url").value.trim()) form.append("source_url", $("#document-url").value.trim());
  form.append("source_type", $("#document-source-type").value);
  form.append("game_version", $("#document-game-version").value.trim());
  form.append("language", $("#document-language").value);
  form.append("keywords", $("#document-keywords").value.trim());
  const submit = $("#document-form button[type=submit]");
  submit.disabled = true;
  try {
    await uploadDocument("/admin/documents", "POST", form, documentUploadProgress);
    $("#document-form").reset(); toast(t("documentUploaded")); await loadDashboard();
  } catch (error) { toast(error.message); }
  finally { submit.disabled = false; }
});
$("#close-knowledge").addEventListener("click", () => $("#knowledge-dialog").close());
$("#knowledge-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData();
  form.append("title", $("#knowledge-title").value.trim());
  form.append("file", new File([$("#knowledge-content").value.trim()], "staff-knowledge.txt", { type: "text/plain" }));
  if ($("#knowledge-url").value.trim()) form.append("source_url", $("#knowledge-url").value.trim());
  form.append("source_type", "official");
  form.append("language", languageSelect.value);
  const submit = $("#knowledge-form button[type=submit]");
  submit.disabled = true;
  try {
    await uploadDocument("/admin/documents", "POST", form, textUploadProgress);
    $("#knowledge-dialog").close();
    toast(t("draftSaved"));
    await loadDashboard();
  } catch (error) { toast(error.message); }
  finally { submit.disabled = false; }
});
async function watchEvents() {
  const sessionToken = adminToken;
  if (!sessionToken) return;
  eventController = new AbortController();
  try {
    const response = await fetch(`${API}/admin/events`, { headers: { Authorization: `Bearer ${sessionToken}` }, signal: eventController.signal });
    if (!response.ok || !response.body) throw new Error("event stream unavailable");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const frames = pending.split("\n\n"); pending = frames.pop();
      if (frames.some((frame) => frame.startsWith("event: refresh"))) await loadDashboard();
    }
  } catch (_) { /* Manual refresh remains available during reconnect. */ }
  if (adminToken === sessionToken && !eventController.signal.aborted) setTimeout(watchEvents, 3000);
}
if (adminToken) loadDashboard().then(watchEvents).catch((error) => { showLogin(); toast(error.message); });
