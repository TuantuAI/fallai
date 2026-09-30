const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const authView = $("#authView");
const appView = $("#appView");
const authMsg = $("#authMsg");
const camMsg = $("#camMsg");
const contactMsg = $("#contactMsg");

let profile = null;
let eventsCache = [];
let liveSocket = null;
let reconnectTimer = null;
let cameraRunning = false;
let alertTimer = null;
let liveMetrics = [];

const pageMeta = {
  monitor: {eyebrow: "TRUNG TÂM GIÁM SÁT", title: "Giám sát trực tiếp"},
  history: {eyebrow: "NHẬT KÝ HỆ THỐNG", title: "Lịch sử cảnh báo"},
  contacts: {eyebrow: "CẤU HÌNH CẢNH BÁO", title: "Người nhận SMS"},
  system: {eyebrow: "THÔNG TIN VẬN HÀNH", title: "Hệ thống & tài khoản"},
};

const postureLabels = {standing: "Đứng", sitting: "Ngồi", lying: "Nằm", unknown: "Chưa xác định"};
const eventLabels = {normal: "Bình thường", falling: "Đang té", postfall: "Sau té ngã", unknown: "Chưa xác định"};
const observationLabels = {clear: "Rõ ràng", partial: "Một phần", occluded: "Bị che khuất", lost: "Mất dấu", unknown: "Chưa xác định"};
const smsLabels = {sent: "Đã gửi", failed: "Thất bại", queued: "Đang chờ", disabled: "Đang tắt", no_contact: "Chưa cấu hình"};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  let body = {};
  try { body = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(body.detail || body.error || `HTTP ${response.status}`);
  return body;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function initials(name) {
  const parts = String(name || "Fall AI").trim().split(/\s+/).filter(Boolean);
  return parts.slice(-2).map((part) => part[0]).join("").toUpperCase() || "FA";
}

function formatDate(value, includeSeconds = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("vi-VN", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", ...(includeSeconds ? {second: "2-digit"} : {}),
  });
}

function dateParts(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return {date: "—", time: "—"};
  return {
    date: date.toLocaleDateString("vi-VN", {day: "2-digit", month: "2-digit", year: "numeric"}),
    time: date.toLocaleTimeString("vi-VN", {hour: "2-digit", minute: "2-digit", second: "2-digit"}),
  };
}

function isToday(value) {
  const date = new Date(value);
  const now = new Date();
  return !Number.isNaN(date.getTime())
    && date.getDate() === now.getDate()
    && date.getMonth() === now.getMonth()
    && date.getFullYear() === now.getFullYear();
}

function safeImageUrl(value) {
  const url = String(value || "");
  return url.startsWith("/img/") ? escapeHtml(url) : "";
}

function setDot(selector, ok) {
  const dot = $(selector);
  if (!dot) return;
  dot.classList.remove("ok", "bad");
  dot.classList.add(ok ? "ok" : "bad");
}

function setMessage(element, text, type = "") {
  if (!element) return;
  element.textContent = text;
  element.classList.remove("success", "error");
  if (type) element.classList.add(type);
}

function showToast(title, detail, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const symbol = type === "success" ? "✓" : type === "error" ? "!" : "i";
  toast.innerHTML = `<span>${symbol}</span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small></div>`;
  $("#toastContainer").appendChild(toast);
  setTimeout(() => toast.remove(), 4200);
}

function updateClock() {
  const clock = $("#videoClock");
  if (clock) clock.textContent = new Date().toLocaleTimeString("vi-VN", {hour12: false});
}
setInterval(updateClock, 1000);
updateClock();

function setCameraState(active, label) {
  $("#monitorStateDot").classList.toggle("active", active);
  $("#monitorStateText").textContent = label;
}

function ensureLiveFeed() {
  const feed = $("#liveFeed");
  if (!cameraRunning || feed.dataset.streaming === "true") return;
  feed.dataset.streaming = "true";
  feed.src = `/api/camera/stream?ts=${Date.now()}`;
}

function clearLiveFeed() {
  const feed = $("#liveFeed");
  feed.dataset.streaming = "false";
  feed.removeAttribute("src");
  $("#videoEmpty").classList.remove("hidden");
}

function applyCameraStatus(camera = {}) {
  cameraRunning = Boolean(camera.running);
  const connected = Boolean(camera.connected);
  const source = camera.source || "Chưa cấu hình";
  const viewers = Number(camera.viewers || 0);

  $("#cameraSourceLabel").textContent = `${camera.name || "Camera máy chủ"} · ${source}`;
  $("#cameraViewerCount").textContent = `${viewers} người xem`;
  $("#systemCameraSource").textContent = source;
  $("#systemCamera").textContent = connected ? "Đang phát" : (cameraRunning ? "Đang kết nối" : "Đã dừng");
  $("#systemCamera").className = connected ? "service-ok" : "";
  $("#startBtn").disabled = cameraRunning;
  $("#stopBtn").disabled = !cameraRunning;

  if (cameraRunning) {
    setCameraState(connected, connected ? "Đang giám sát" : "Đang kết nối…");
    ensureLiveFeed();
    if (camera.has_frame) $("#videoEmpty").classList.add("hidden");
    if (camera.error) setMessage(camMsg, `Camera đang thử kết nối lại: ${camera.error}`, "error");
    else if (connected) setMessage(camMsg, "Camera máy chủ đang hoạt động. AI phân tích độc lập với thiết bị xem.", "success");
    else setMessage(camMsg, "Máy chủ đang mở nguồn camera…");
  } else {
    setCameraState(false, "Chưa kết nối");
    clearLiveFeed();
    if (camera.error) setMessage(camMsg, `Camera đã dừng: ${camera.error}`, "error");
  }
}

function disconnectLiveSocket() {
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (liveSocket) {
    liveSocket.onclose = null;
    liveSocket.close();
    liveSocket = null;
  }
}

function connectLiveSocket() {
  disconnectLiveSocket();
  if (!profile) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  liveSocket = new WebSocket(`${scheme}://${location.host}/ws/live`);
  liveSocket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.camera) applyCameraStatus(payload.camera);
    if (payload.type === "frame" && payload.result) {
      if (payload.result.ok) updateResult(payload.result);
      else setMessage(camMsg, payload.result.error || "Lỗi xử lý khung hình", "error");
    }
    if (payload.type === "error") setMessage(camMsg, payload.error || "Lỗi kết nối", "error");
  };
  liveSocket.onerror = () => setMessage(camMsg, "Mất kênh dữ liệu trực tiếp; hệ thống đang kết nối lại…", "error");
  liveSocket.onclose = () => {
    liveSocket = null;
    if (profile) reconnectTimer = setTimeout(connectLiveSocket, 2000);
  };
}

function updateContactPreview(contact) {
  const enabled = Boolean(contact?.enabled);
  const hasContact = Boolean(contact?.name && contact?.phone);
  $("#contactPreviewName").textContent = contact?.name || "Chưa có người nhận";
  $("#contactPreviewPhone").textContent = contact?.phone || "Thêm số điện thoại để kích hoạt cảnh báo.";
  $("#contactPreviewStatus").textContent = hasContact ? (enabled ? "Đang nhận cảnh báo" : "Đã tạm dừng") : "Chưa kích hoạt";
  $("#contactPreviewDot").classList.toggle("ok", hasContact && enabled);
  $("#contactPreviewDot").classList.toggle("bad", hasContact && !enabled);
  const badge = $("#contactStatusBadge");
  badge.classList.remove("safe", "warning", "muted");
  if (!hasContact) {
    badge.textContent = "CHƯA CẤU HÌNH";
    badge.classList.add("muted");
  } else if (enabled) {
    badge.textContent = "ĐANG HOẠT ĐỘNG";
    badge.classList.add("safe");
  } else {
    badge.textContent = "ĐÃ TẠM DỪNG";
    badge.classList.add("warning");
  }
}

function fillProfile(data) {
  const user = data.user;
  const avatarText = initials(user.name);
  $("#userName").textContent = user.name;
  $("#sidebarUserName").textContent = user.name;
  $("#sidebarUserEmail").textContent = user.email;
  $("#userAvatar").textContent = avatarText;
  $("#accountAvatar").textContent = avatarText;
  $("#accountName").textContent = user.name;
  $("#accountEmail").textContent = user.email;
  $("#accountId").textContent = `#${user.id}`;
  $("#accountCreated").textContent = formatDate(user.created_at);

  $("#poseStatus").textContent = data.ai.pose_ready ? "Sẵn sàng" : "Lỗi";
  $("#gruStatus").textContent = data.ai.gru_ready ? "Sẵn sàng" : "Thiếu model";
  $("#smsStatus").textContent = data.sms_on ? "Đã bật" : "Đang tắt";
  $("#systemPose").textContent = data.ai.pose_ready ? "Sẵn sàng" : "Lỗi";
  $("#systemGru").textContent = data.ai.gru_ready ? "Sẵn sàng" : "Thiếu model";
  $("#systemSms").textContent = data.sms_on ? "Đã bật" : "Đang tắt";
  $("#systemPose").className = data.ai.pose_ready ? "service-ok" : "";
  $("#systemGru").className = data.ai.gru_ready ? "service-ok" : "";
  $("#systemSms").className = data.sms_on ? "service-ok" : "";
  $("#systemFps").textContent = `${data.sample_fps || 8} FPS`;
  setDot("#poseDot", data.ai.pose_ready);
  setDot("#gruDot", data.ai.gru_ready);
  setDot("#smsDot", data.sms_on);
  applyCameraStatus(data.camera || {});

  const form = $("#contactForm");
  if (data.contact) {
    form.elements.name.value = data.contact.name;
    form.elements.phone.value = data.contact.phone;
    form.elements.enabled.checked = Boolean(data.contact.enabled);
  } else {
    form.reset();
    form.elements.enabled.checked = true;
  }
  updateContactPreview(data.contact);
}

function navigate(route, updateHash = true) {
  if (!pageMeta[route]) route = "monitor";
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.route === route));
  $$(".page").forEach((page) => page.classList.toggle("active", page.dataset.page === route));
  $("#pageEyebrow").textContent = pageMeta[route].eyebrow;
  $("#pageTitle").textContent = pageMeta[route].title;
  document.title = `FallAI · ${pageMeta[route].title}`;
  document.body.classList.remove("sidebar-open");
  window.scrollTo({top: 0, behavior: "smooth"});
  if (updateHash && location.hash !== `#${route}`) history.replaceState(null, "", `#${route}`);
  if (route === "history") loadEvents();
}

function showApp(data) {
  profile = data;
  authView.classList.add("hidden");
  appView.classList.remove("hidden");
  $("#logoutBtn").classList.remove("hidden");
  fillProfile(data);
  navigate(location.hash.slice(1) || "monitor", false);
  loadEvents();
  connectLiveSocket();
}

function showAuth() {
  disconnectLiveSocket();
  clearLiveFeed();
  authView.classList.remove("hidden");
  appView.classList.add("hidden");
  $("#logoutBtn").classList.add("hidden");
  document.body.classList.remove("sidebar-open");
  document.title = "FallAI · Đăng nhập";
}

async function bootstrap() {
  try { showApp(await api("/api/me")); }
  catch (_) { showAuth(); }
}

$$(".auth-tab").forEach((tab) => tab.addEventListener("click", () => {
  $$(".auth-tab").forEach((item) => item.classList.remove("active"));
  tab.classList.add("active");
  const login = tab.dataset.authTab === "login";
  $("#loginForm").classList.toggle("hidden", !login);
  $("#registerForm").classList.toggle("hidden", login);
  setMessage(authMsg, "");
}));

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(authMsg, "Đang xác thực tài khoản…");
  const values = Object.fromEntries(new FormData(event.target));
  try {
    await api("/api/login", {method: "POST", body: JSON.stringify(values)});
    showApp(await api("/api/me"));
    showToast("Đăng nhập thành công", "Chào mừng bạn trở lại FallAI.", "success");
  } catch (error) { setMessage(authMsg, error.message, "error"); }
});

$("#registerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(authMsg, "Đang tạo tài khoản…");
  const values = Object.fromEntries(new FormData(event.target));
  try {
    await api("/api/register", {method: "POST", body: JSON.stringify(values)});
    showApp(await api("/api/me"));
    showToast("Tạo tài khoản thành công", "Hệ thống đã sẵn sàng để cấu hình.", "success");
  } catch (error) { setMessage(authMsg, error.message, "error"); }
});

async function logout() {
  disconnectLiveSocket();
  try { await api("/api/logout", {method: "POST"}); } catch (_) {}
  profile = null;
  eventsCache = [];
  showAuth();
}

["#logoutBtn", "#sidebarLogoutBtn", "#accountLogoutBtn"].forEach((selector) => {
  $(selector).addEventListener("click", logout);
});

$$(".nav-item").forEach((item) => item.addEventListener("click", () => navigate(item.dataset.route)));
$$('[data-go]').forEach((item) => item.addEventListener("click", () => navigate(item.dataset.go)));
$("#menuBtn").addEventListener("click", () => document.body.classList.add("sidebar-open"));
$("#closeMenuBtn").addEventListener("click", () => document.body.classList.remove("sidebar-open"));
$("#sidebarBackdrop").addEventListener("click", () => document.body.classList.remove("sidebar-open"));
window.addEventListener("hashchange", () => { if (profile) navigate(location.hash.slice(1), false); });

$("#contactForm").addEventListener("input", (event) => {
  const form = event.currentTarget;
  updateContactPreview({
    name: form.elements.name.value.trim(),
    phone: form.elements.phone.value.trim(),
    enabled: form.elements.enabled.checked,
  });
});

$("#contactForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(contactMsg, "Đang lưu thông tin…");
  const data = new FormData(event.target);
  const payload = {
    name: data.get("name"), phone: data.get("phone"), enabled: data.get("enabled") === "on",
  };
  try {
    const result = await api("/api/contact", {method: "PUT", body: JSON.stringify(payload)});
    profile.contact = result.contact;
    updateContactPreview(result.contact);
    setMessage(contactMsg, "Đã lưu người nhận cảnh báo.", "success");
    showToast("Đã lưu thông tin", "Người nhận cảnh báo SMS đã được cập nhật.", "success");
  } catch (error) {
    setMessage(contactMsg, error.message, "error");
    showToast("Không thể lưu", error.message, "error");
  }
});

$("#testSmsBtn").addEventListener("click", async () => {
  const form = $("#contactForm");
  if (!form.reportValidity()) return;
  const phone = form.elements.phone.value;
  setMessage(contactMsg, "Đang đưa SMS thử vào hàng đợi…");
  try {
    const result = await api("/api/sms/test", {method: "POST", body: JSON.stringify({phone})});
    if (result.ok) {
      setMessage(contactMsg, "Đã đưa SMS thử vào hàng đợi.", "success");
      showToast("SMS đã được xếp hàng", "Theo dõi điện thoại để kiểm tra tin nhắn.", "success");
    } else {
      setMessage(contactMsg, "SMS_ON đang tắt; kiểm tra cấu hình Dcom.", "error");
      showToast("Dịch vụ SMS đang tắt", "Hãy bật SMS_ON trong file cấu hình.", "error");
    }
  } catch (error) {
    setMessage(contactMsg, error.message, "error");
    showToast("Không gửi được SMS thử", error.message, "error");
  }
});

function eventMatches(item, query, filter) {
  const haystack = [item.posture, item.event_label, item.observation, item.sms_status, formatDate(item.created_at)].join(" ").toLowerCase();
  const textMatch = !query || haystack.includes(query.toLowerCase());
  if (!textMatch || filter === "all") return textMatch;
  if (filter === "sent" || filter === "failed") return item.sms_status === filter;
  return item.event_label === filter;
}

function smsClass(status) {
  if (status === "sent") return "sent";
  if (status === "failed") return "failed";
  if (status === "queued") return "queued";
  return "neutral";
}

function imageCell(item, className = "event-thumb") {
  const image = safeImageUrl(item.image);
  if (!image) return `<span class="${className} no-image"><svg><use href="#i-camera"></use></svg></span>`;
  return `<a href="${image}" target="_blank" rel="noopener" aria-label="Mở ảnh sự kiện"><img class="${className}" src="${image}" alt="Ảnh sự kiện té ngã"></a>`;
}

function renderRecentEvents() {
  const container = $("#recentEvents");
  const recent = eventsCache.slice(0, 3);
  if (!recent.length) {
    container.innerHTML = '<div class="empty-state small"><span><svg><use href="#i-history"></use></svg></span><p>Chưa có cảnh báo được ghi nhận.</p></div>';
    return;
  }
  container.innerHTML = recent.map((item) => {
    const event = eventLabels[item.event_label] || item.event_label;
    return `<article class="recent-event">
      ${imageCell(item, "event-thumb")}
      <div><strong>${escapeHtml(event)}</strong><small>${escapeHtml(formatDate(item.created_at))}</small></div>
      <span class="confidence-ring">${Math.round(Number(item.score || 0) * 100)}%</span>
    </article>`;
  }).join("");
}

function renderHistory() {
  const query = $("#historySearch").value.trim();
  const filter = $("#historyFilter").value;
  const events = eventsCache.filter((item) => eventMatches(item, query, filter));
  const rows = $("#eventRows");
  $("#historyCount").textContent = `Hiển thị ${events.length} sự kiện`;
  if (!events.length) {
    rows.innerHTML = '<tr><td colspan="7"><div class="empty-state"><span><svg><use href="#i-history"></use></svg></span><strong>Không có dữ liệu phù hợp</strong><p>Thử thay đổi nội dung tìm kiếm hoặc bộ lọc.</p></div></td></tr>';
    return;
  }
  rows.innerHTML = events.map((item) => {
    const parts = dateParts(item.created_at);
    const posture = postureLabels[item.posture] || item.posture;
    const event = eventLabels[item.event_label] || item.event_label;
    const observation = observationLabels[item.observation] || item.observation;
    const smsStatus = item.sms_status || "no_contact";
    const score = Math.max(0, Math.min(100, Math.round(Number(item.score || 0) * 100)));
    return `<tr>
      <td><div class="event-time"><strong>${escapeHtml(parts.time)}</strong><small>${escapeHtml(parts.date)}</small></div></td>
      <td>${imageCell(item)}</td>
      <td><div class="analysis-cell"><span class="event-tag ${escapeHtml(item.event_label)}">${escapeHtml(event)}</span><small>Tư thế: ${escapeHtml(posture)}</small></div></td>
      <td><div class="score-bar"><div><i style="width:${score}%"></i></div><strong>${score}%</strong></div></td>
      <td><div class="observation-cell"><span class="observation-tag">${escapeHtml(observation)}</span><small>${Number(item.visible || 0)}/17 keypoint</small></div></td>
      <td><div class="analysis-cell"><strong>${Number(item.ai_ms || 0).toFixed(0)} ms</strong><small>AI pipeline</small></div></td>
      <td><span class="sms-tag ${smsClass(smsStatus)}">${escapeHtml(smsLabels[smsStatus] || smsStatus)}</span>${item.sms_ms ? `<small class="sms-time">${Number(item.sms_ms).toFixed(0)} ms</small>` : ""}</td>
    </tr>`;
  }).join("");
}

function updateEventStats(result) {
  const total = Number(result.stats?.total || 0);
  const today = eventsCache.filter((item) => isToday(item.created_at)).length;
  const sent = eventsCache.filter((item) => item.sms_status === "sent").length;
  $("#totalEvents").textContent = total.toLocaleString("vi-VN");
  $("#todayEvents").textContent = today.toLocaleString("vi-VN");
  $("#avgAiMs").textContent = `${Number(result.stats?.ai_ms_avg || 0).toFixed(0)} ms`;
  $("#sentSms").textContent = sent.toLocaleString("vi-VN");
  $("#navEventCount").textContent = total > 99 ? "99+" : String(total);
}

async function loadEvents() {
  if (!profile) return;
  try {
    const result = await api("/api/events?limit=100");
    eventsCache = result.events || [];
    updateEventStats(result);
    renderRecentEvents();
    renderHistory();
  } catch (error) {
    setMessage(camMsg, error.message, "error");
    showToast("Không tải được lịch sử", error.message, "error");
  }
}

$("#refreshBtn").addEventListener("click", async () => {
  const button = $("#refreshBtn");
  button.disabled = true;
  await loadEvents();
  button.disabled = false;
  showToast("Đã làm mới", "Lịch sử cảnh báo đã được cập nhật.", "success");
});
$("#historySearch").addEventListener("input", renderHistory);
$("#historyFilter").addEventListener("change", renderHistory);

function updatePolicyBadge(data) {
  const badge = $("#policyBadge");
  badge.classList.remove("safe", "warning", "danger");
  if (data.alert || data.event === "falling" || data.event === "postfall") {
    badge.textContent = data.alert ? "CẢNH BÁO" : "NGUY CƠ";
    badge.classList.add(data.alert ? "danger" : "warning");
  } else {
    badge.textContent = "AN TOÀN";
    badge.classList.add("safe");
  }
}

function updateResult(data) {
  const frameMs = Number(data.timing?.frame_ms || data.timing?.ai_ms || 0);
  liveMetrics.push({
    time: new Date().toISOString(), frame_id: data.frame_id, frame_ms: frameMs,
    ai_ms: data.timing?.ai_ms || 0, pose_ms: data.timing?.pose_ms || 0,
    gru_ms: data.timing?.gru_ms || 0, fps: data.timing?.fps || 0,
    visible: data.visible || 0, observation: data.observation || "unknown",
    posture: data.posture || "unknown", event: data.event || "unknown",
    event_score: data.event_score || 0, policy: data.policy_state || "unknown",
  });
  if (liveMetrics.length > 20000) liveMetrics.shift();

  $("#posture").textContent = postureLabels[data.posture] || data.posture || "Chưa xác định";
  $("#postureScore").textContent = `${Math.round((data.posture_score || 0) * 100)}%`;
  $("#eventLabel").textContent = eventLabels[data.event] || data.event || "Chưa xác định";
  $("#eventScore").textContent = `${Math.round((data.event_score || 0) * 100)}%`;
  $("#visible").textContent = `${data.visible || 0}/17`;
  $("#observation").textContent = observationLabels[data.observation] || data.observation || "—";
  const observationBadge = $("#obsBadge");
  observationBadge.textContent = String(data.observation || "unknown").toUpperCase();
  observationBadge.className = `observation-badge ${data.observation || "unknown"}`;
  $("#aiMs").textContent = `${Math.round(data.timing?.ai_ms || 0)} ms`;
  $("#p95Ms").textContent = `P95 ${Math.round(data.timing?.p95_ms || 0)} ms`;
  $("#e2eMs").textContent = `${Math.round(frameMs)} ms`;
  $("#fps").textContent = `${Number(data.timing?.fps || 0).toFixed(1)} FPS`;
  $("#policy").textContent = String(data.policy_state || "normal").toUpperCase();
  $("#warmup").textContent = data.model_ready
    ? (data.warmup ? `Còn ${data.warmup} frame để đủ chuỗi` : "GRU đang dự đoán liên tục")
    : "Chưa tìm thấy checkpoint GRU";
  updatePolicyBadge(data);

  if (data.alert) {
    clearTimeout(alertTimer);
    $("#alertBar").classList.remove("hidden");
    showToast("Cảnh báo té ngã", "Sự kiện đã được lưu và SMS đang được xử lý.", "error");
    alertTimer = setTimeout(() => $("#alertBar").classList.add("hidden"), 12000);
    loadEvents();
  }
}

async function startCamera() {
  setMessage(camMsg, "Đang yêu cầu máy chủ mở camera…");
  setCameraState(false, "Đang kết nối…");
  $("#startBtn").disabled = true;
  try {
    const result = await api("/api/camera/start", {method: "POST"});
    applyCameraStatus(result.camera);
    ensureLiveFeed();
    showToast(
      result.started ? "Đã khởi động giám sát" : "Camera đang hoạt động",
      "Máy chủ đang xử lý camera; bạn có thể theo dõi từ nhiều thiết bị.",
      "success",
    );
  } catch (error) {
    setMessage(camMsg, `Không khởi động được camera: ${error.message}`, "error");
    showToast("Không khởi động được camera", error.message, "error");
    $("#startBtn").disabled = false;
  }
}

async function stopCamera() {
  $("#stopBtn").disabled = true;
  setMessage(camMsg, "Đang dừng camera trên máy chủ…");
  try {
    const result = await api("/api/camera/stop", {method: "POST"});
    applyCameraStatus(result.camera);
    setMessage(camMsg, "Camera máy chủ đã dừng. Website không còn nhận hình ảnh.");
    showToast("Đã dừng giám sát", "Mô hình và dịch vụ SMS vẫn được nạp trên máy chủ.", "success");
  } catch (error) {
    setMessage(camMsg, `Không dừng được camera: ${error.message}`, "error");
    $("#stopBtn").disabled = false;
  }
}

$("#liveFeed").addEventListener("load", () => $("#videoEmpty").classList.add("hidden"));
$("#liveFeed").addEventListener("error", () => {
  if (cameraRunning) $("#videoEmpty").classList.remove("hidden");
  $("#liveFeed").dataset.streaming = "false";
});
$("#startBtn").addEventListener("click", startCamera);
$("#stopBtn").addEventListener("click", stopCamera);
$("#dismissAlertBtn").addEventListener("click", () => $("#alertBar").classList.add("hidden"));

$("#downloadMetricsBtn").addEventListener("click", () => {
  if (!liveMetrics.length) {
    setMessage(camMsg, "Chưa có khung hình để xuất chỉ số.", "error");
    showToast("Chưa có dữ liệu", "Hãy bật camera và chờ AI phân tích.", "error");
    return;
  }
  const keys = Object.keys(liveMetrics[0]);
  const csv = [keys.join(","), ...liveMetrics.map((row) => keys.map((key) => row[key]).join(","))].join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
  link.download = `fallai_live_${new Date().toISOString().replace(/[:.]/g, "-")}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
  showToast("Đã xuất dữ liệu", "File CSV chỉ số thời gian thực đã được tải xuống.", "success");
});

window.addEventListener("beforeunload", () => {
  disconnectLiveSocket();
  clearLiveFeed();
});
bootstrap();
