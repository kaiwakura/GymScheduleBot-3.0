// GymScheduleBot front end (A+ Studio): the week calendar, dragging, workout details,
// Preferences, Google sync and the AI Coach chat. Plain JavaScript, no build step.

const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
const EVENTS_KEY = "gymschedulebot.events.v1";
const SAVED_EVENTS_KEY = "gymschedulebot.events.saved.v1"; // your own commitments while a sample is loaded
const HISTORY_KEY = "gymschedulebot.history.v1";
const MOVES_KEY = "gymschedulebot.moves.v1";
const planRegion = document.getElementById("plan-region");
const messages = document.getElementById("messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendButton = chatForm.querySelector(".send");
const chatLimit = document.getElementById("chat-limit");
const MAX_MESSAGE = Number(chatInput.dataset.maxLength) || 2000; // the server refuses longer messages
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

// ------------------------------------------------------- browser storage ---
// Your commitments, recent workouts and moved workouts live in this browser, so they
// survive server restarts. The server validates them on every request.
function loadList(key) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
function saveList(key, list) {
  try {
    if (list === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(list));
  } catch {
    /* storage full or blocked: everything still works for this page view */
  }
}
const hasStored = (key) => {
  try {
    return localStorage.getItem(key) !== null;
  } catch {
    return false;
  }
};
const firstVisit = !hasStored(EVENTS_KEY);
let myEvents = loadList(EVENTS_KEY);
let myHistory = loadList(HISTORY_KEY);
let myMoves = loadList(MOVES_KEY);
let currentWeek = document.body.dataset.week || "";
let appliedPrefs = JSON.parse(document.getElementById("prefs-data").textContent || "{}");
const isSampleEvent = (ev) => String(ev.id).startsWith("sample-");
const newEventId = () => `e_${Math.random().toString(16).slice(2, 10)}`;

// --------------------------------------------------------------- server ---
async function callServer(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(payload),
  });
  return { ok: res.ok, data: await res.json() };
}

// A yes/no question. The interactive demo (which runs this page without a server) swaps
// in its own server and dialog through window.gsbServer and window.gsbConfirm.
const ask = (text) => (window.gsbConfirm ? window.gsbConfirm(text) : Promise.resolve(window.confirm(text)));

async function post(url, body = {}) {
  const payload = { week: currentWeek, ...body, events: myEvents, history: myHistory, moves: myMoves, timezone: browserTz };
  const { ok, data } = await (window.gsbServer || callServer)(url, payload);
  if (!ok) throw new Error(data.error || "Something went wrong.");
  if (data.html) {
    closePopover();
    planRegion.innerHTML = data.html;
    applyMode();
    revealWorkouts();
  }
  if (data.week) currentWeek = document.body.dataset.week = data.week;
  if (Array.isArray(data.events)) saveList(EVENTS_KEY, (myEvents = data.events));
  if (Array.isArray(data.history)) saveList(HISTORY_KEY, (myHistory = data.history));
  if (Array.isArray(data.moves)) saveList(MOVES_KEY, (myMoves = data.moves));
  if (data.settings) appliedPrefs = data.settings;
  return data;
}
const replan = (extra = {}) => post("/api/plan", extra);

async function withLoading(task) {
  planRegion.classList.add("loading");
  try {
    return await task();
  } finally {
    planRegion.classList.remove("loading");
  }
}

// If the calendar is taller than the screen, scroll it so the first workout is in view.
function revealWorkouts() {
  const wrap = planRegion.querySelector(".week-wrap");
  const workouts = [...planRegion.querySelectorAll(".workout")];
  if (!wrap || !workouts.length || wrap.scrollHeight <= wrap.clientHeight) return;
  const firstTop = Math.min(...workouts.map((w) => w.offsetTop));
  wrap.scrollTop = Math.max(0, firstTop - 80);
}

// ----------------------------------------------------------- small UI ---
const toast = document.getElementById("toast");
let toastTimer = null;
function showToast(text) {
  document.getElementById("toast-text").textContent = text;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.hidden = true), 2600);
}

const undoBar = document.getElementById("undo-bar");
let undoMoves = null;
function showUndo(text, previousMoves) {
  undoMoves = previousMoves;
  document.getElementById("undo-text").textContent = text;
  undoBar.hidden = false;
}
function hideUndo() {
  undoBar.hidden = true;
  undoMoves = null;
}

const menu = document.getElementById("menu");
function toggleMenu(open = menu.hidden) {
  menu.hidden = !open;
  document.querySelector('[data-action="toggle-menu"]').setAttribute("aria-expanded", String(open));
}

// ------------------------------------------------------- calendar mode ---
// Plan mode: click empty time to add a commitment. Edit availability: drag to mark time
// you can't train; workouts fade so it's clear what you're editing.
let mode = "plan";
function applyMode() {
  planRegion.classList.toggle("avail-mode", mode === "availability");
  planRegion.querySelectorAll('[data-action="mode"]').forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
}

// --------------------------------------------------------- time helpers ---
const pad = (n) => String(n).padStart(2, "0");
const toHHMM = (minutes) => `${pad(Math.floor(minutes / 60) % 24)}:${pad(minutes % 60)}`;
const toMinutes = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};
function clockLabel(minutes) {
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return `${((h + 11) % 12) + 1}:${pad(m)} ${h < 12 ? "AM" : "PM"}`;
}
// "2026-11-27T07:00:00-08:00" -> date and minutes after midnight, in the time zone it was written in.
function splitIso(iso) {
  return { date: iso.slice(0, 10), minutes: toMinutes(iso.slice(11, 16)) };
}
function colRange(col) {
  const first = Number(col.dataset.firstHour) * 60;
  return [first, first + Number(col.dataset.hours) * 60];
}
function placeBlock(block, col, start, end) {
  const [first, last] = colRange(col);
  block.style.top = `${((start - first) / (last - first)) * 100}%`;
  block.style.height = `${((end - start) / (last - first)) * 100}%`;
  const time = block.querySelector(".e-time, .w-range");
  if (time) time.textContent = `${clockLabel(start)} – ${clockLabel(end)}`;
}
const dayCol = (date) => planRegion.querySelector(`.day-col[data-date="${date}"]`);

// ------------------------------------------------------- commitment editor ---
const dialog = document.getElementById("event-dialog");
const eventForm = document.getElementById("event-form");
const daySelect = document.getElementById("event-day");
const deleteBtn = document.getElementById("event-delete");
const errorBox = document.getElementById("event-error");
let editingId = null;

function fillDays(selectedDate) {
  daySelect.innerHTML = "";
  planRegion.querySelectorAll(".day-col[data-date]").forEach((col) => {
    const opt = document.createElement("option");
    opt.value = `${col.dataset.date}|${col.dataset.weekday}`;
    opt.textContent = col.dataset.label;
    if (col.dataset.date === selectedDate) opt.selected = true;
    daySelect.appendChild(opt);
  });
}

function openEditor({ date, eventId, startMinutes, unavailable = false }) {
  errorBox.hidden = true;
  editingId = eventId || null;
  const ev = myEvents.find((x) => x.id === editingId);
  const marking = ev ? ev.kind === "unavailable" : unavailable;
  document.getElementById("event-dialog-title").textContent = ev
    ? marking ? "Edit time you can't train" : "Edit commitment"
    : marking ? "Mark time you can't train" : "Add a commitment";
  deleteBtn.hidden = !ev;
  eventForm.title.value = ev ? ev.title : marking ? "Can't train" : "";
  const startAt = startMinutes ?? 9 * 60;
  eventForm.start.value = ev ? ev.start : toHHMM(startAt);
  eventForm.end.value = ev ? ev.end : toHHMM(Math.min(startAt + 60, 23 * 60 + 59));
  eventForm.weekly.checked = ev ? ev.repeat === "weekly" : !marking;
  eventForm.unavailable.checked = marking;
  let dateForSelect = date;
  if (ev && ev.repeat === "once") dateForSelect = ev.date;
  if (ev && ev.repeat === "weekly") {
    const match = [...planRegion.querySelectorAll(".day-col[data-weekday]")].find((b) => Number(b.dataset.weekday) === ev.weekday);
    dateForSelect = match ? match.dataset.date : date;
  }
  fillDays(dateForSelect || planRegion.querySelector(".day-col.today")?.dataset.date);
  dialog.showModal();
  eventForm.title.focus();
}

eventForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = eventForm.title.value.trim();
  const start = eventForm.start.value;
  const end = eventForm.end.value;
  if (!title || !start || !end || start >= end) {
    errorBox.textContent = "Add a title and make sure it ends after it starts.";
    errorBox.hidden = false;
    return;
  }
  const [date, weekday] = daySelect.value.split("|");
  const event = { id: editingId || newEventId(), title, start, end };
  if (eventForm.weekly.checked) Object.assign(event, { repeat: "weekly", weekday: Number(weekday) });
  else Object.assign(event, { repeat: "once", date });
  if (eventForm.unavailable.checked) event.kind = "unavailable";
  myEvents = editingId ? myEvents.map((x) => (x.id === editingId ? event : x)) : [...myEvents, event];
  saveList(EVENTS_KEY, myEvents);
  dialog.close();
  await withLoading(() => replan());
});
deleteBtn.addEventListener("click", async () => {
  myEvents = myEvents.filter((x) => x.id !== editingId);
  saveList(EVENTS_KEY, myEvents);
  dialog.close();
  await withLoading(() => replan());
});
document.getElementById("event-cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (e) => {
  if (e.target === dialog) dialog.close();
});

// ------------------------------------------------------- workout details ---
let openPop = null;

function positionPopover(pop, block) {
  if (window.matchMedia("(max-width: 640px)").matches) {
    pop.style.left = pop.style.top = "";
    return; // a bottom sheet on phones (see style.css)
  }
  const rect = block.getBoundingClientRect();
  const width = pop.offsetWidth, height = pop.offsetHeight, gap = 8;
  let left = rect.right + gap;
  if (left + width > window.innerWidth - 12) left = rect.left - width - gap;
  const top = Math.min(Math.max(12, rect.top - 6), window.innerHeight - height - 12);
  pop.style.left = `${Math.max(12, left)}px`;
  pop.style.top = `${Math.max(12, top)}px`;
}

function openPopover(block) {
  closePopover();
  const pop = document.getElementById(`pop-${block.dataset.workout}`);
  if (!pop) return;
  pop.hidden = false;
  block.setAttribute("aria-expanded", "true");
  block.classList.add("selected");
  positionPopover(pop, block);
  openPop = { pop, block };
  pop.querySelector(".icon-btn")?.focus({ preventScroll: true });
}

function closePopover() {
  if (!openPop) return;
  openPop.pop.hidden = true;
  openPop.block.setAttribute("aria-expanded", "false");
  openPop.block.classList.remove("selected");
  openPop.pop.querySelectorAll("details").forEach((d) => (d.open = false));
  clearSuggestions();
  openPop = null;
}

// "Other available times": outline them on the calendar while the list is open.
function showSuggestions(block, starts) {
  clearSuggestions();
  const length = Number(block.dataset.length) || 60;
  for (const iso of starts) {
    const { date, minutes } = splitIso(iso);
    const col = dayCol(date);
    if (!col) continue;
    const hint = document.createElement("div");
    hint.className = "block suggestion";
    hint.setAttribute("aria-hidden", "true");
    placeBlock(hint, col, minutes, minutes + length);
    hint.textContent = clockLabel(minutes);
    col.appendChild(hint);
  }
}
function clearSuggestions() {
  planRegion.querySelectorAll(".suggestion").forEach((s) => s.remove());
}
planRegion.addEventListener(
  "toggle",
  (e) => {
    const details = e.target;
    if (!details.classList?.contains("pop-others") || !openPop) return;
    if (details.open) {
      const starts = [...details.querySelectorAll("[data-start]")].map((b) => b.dataset.start);
      showSuggestions(openPop.block, starts);
    } else clearSuggestions();
    positionPopover(openPop.pop, openPop.block);
  },
  true
);
window.addEventListener("resize", () => openPop && positionPopover(openPop.pop, openPop.block));
planRegion.addEventListener("scroll", () => openPop && positionPopover(openPop.pop, openPop.block), true);

async function moveWorkout(block, start) {
  const previous = myMoves.map((m) => ({ ...m }));
  closePopover();
  try {
    const data = await withLoading(() => replan({ move: { name: block.dataset.name, from: block.dataset.start, start } }));
    if (data.undo) showUndo(data.undo, previous);
  } catch (err) {
    addMessage(err.message, "coach");
  }
}

// ---------------------------------------------- drag: commitments and workouts ---
// Drag a commitment or a workout up/down or to another day to move it; drag a commitment's
// bottom edge to change how long it is. Times snap to 15 minutes. A press without movement
// is a click. While a workout is dragged, its suggested times are outlined as drop hints.
const SNAP = 15;
let drag = null;
let swallowClick = false;

// The click that ends a drag shouldn't also open the editor or the details.
function swallowNextClick() {
  swallowClick = true;
  setTimeout(() => (swallowClick = false), 0);
}

planRegion.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  const col = e.target.closest(".day-col");
  if (mode === "availability" && col && e.target === col) return startPaint(e, col);
  const block = e.target.closest(".event.mine, .workout.draggable");
  if (!block || (mode === "availability" && block.classList.contains("workout"))) return;
  const isWorkout = block.classList.contains("workout");
  let start, end, ev = null;
  if (isWorkout) {
    start = splitIso(block.dataset.start).minutes;
    end = start + (Number(block.dataset.length) || 60);
  } else {
    ev = myEvents.find((x) => x.id === block.dataset.eventId);
    if (!ev) return;
    start = toMinutes(ev.start);
    end = toMinutes(ev.end);
  }
  drag = {
    block, ev, isWorkout, col: block.closest(".day-col"), targetCol: block.closest(".day-col"),
    mode: e.target.closest(".resize-handle") ? "resize" : "move",
    x: e.clientX, y: e.clientY, start, end, newStart: start, newEnd: end, moved: false,
  };
  block.setPointerCapture(e.pointerId);
});

planRegion.addEventListener("pointermove", (e) => {
  if (paint) return movePaint(e);
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (!drag.moved && Math.hypot(dx, dy) < 5) return;
  if (!drag.moved) {
    drag.moved = true;
    closePopover();
    drag.block.classList.add("dragging");
    drag.block.style.left = "4px";
    drag.block.style.width = "calc(100% - 8px)";
    if (drag.isWorkout) {
      const pop = document.getElementById(`pop-${drag.block.dataset.workout}`);
      const starts = pop ? [...pop.querySelectorAll(".pop-others [data-start]")].map((b) => b.dataset.start) : [];
      showSuggestions(drag.block, starts);
    }
  }
  const [first, last] = colRange(drag.col);
  const perMinute = drag.col.getBoundingClientRect().height / (last - first);
  const delta = Math.round(dy / perMinute / SNAP) * SNAP;
  const length = drag.end - drag.start;
  if (drag.mode === "resize") {
    drag.newEnd = Math.min(Math.max(drag.end + delta, drag.start + SNAP), last);
  } else {
    drag.newStart = Math.min(Math.max(drag.start + delta, first), last - length);
    drag.newEnd = drag.newStart + length;
    const over = document.elementsFromPoint(e.clientX, e.clientY).find((el) => el.classList?.contains("day-col"));
    if (over && over !== drag.targetCol && !(drag.isWorkout && over.classList.contains("past"))) {
      drag.targetCol = over;
      over.appendChild(drag.block);
    }
  }
  placeBlock(drag.block, drag.targetCol, drag.newStart, drag.newEnd);
});

async function endDrag() {
  if (paint) return endPaint();
  if (!drag) return;
  const d = drag;
  drag = null;
  if (!d.moved) return; // a plain click
  swallowNextClick();
  clearSuggestions();
  if (d.isWorkout) {
    if (d.targetCol === d.col && d.newStart === d.start) return;
    return moveWorkout(d.block, `${d.targetCol.dataset.date}T${toHHMM(d.newStart)}`);
  }
  const updated = { ...d.ev, start: toHHMM(d.newStart), end: toHHMM(d.newEnd) };
  if (d.targetCol !== d.col) {
    if (updated.repeat === "weekly") updated.weekday = Number(d.targetCol.dataset.weekday);
    else updated.date = d.targetCol.dataset.date;
  }
  myEvents = myEvents.map((x) => (x.id === updated.id ? updated : x));
  saveList(EVENTS_KEY, myEvents);
  await withLoading(() => replan());
}
planRegion.addEventListener("pointerup", endDrag);
planRegion.addEventListener("pointercancel", endDrag);
document.addEventListener(
  "click",
  (e) => {
    if (swallowClick) {
      e.stopPropagation();
      e.preventDefault();
      swallowClick = false;
    }
  },
  true
);

// ----------------------------------------- Edit availability: paint "can't train" ---
let paint = null;
function minutesAt(col, clientY) {
  const [first, last] = colRange(col);
  const rect = col.getBoundingClientRect();
  const raw = first + ((clientY - rect.top) / rect.height) * (last - first);
  return Math.min(Math.max(Math.round(raw / SNAP) * SNAP, first), last);
}
// The block grows from where you pressed (anchor) toward the pointer, up or down.
function startPaint(e, col) {
  const anchor = minutesAt(col, e.clientY);
  const block = document.createElement("div");
  block.className = "block event unavailable painting";
  block.innerHTML = '<span class="e-title">Can\'t train</span><span class="e-time"></span>';
  col.appendChild(block);
  paint = { col, block, anchor, start: anchor, end: anchor + 60, y: e.clientY, moved: false };
  placeBlock(block, col, paint.start, paint.end);
  col.setPointerCapture(e.pointerId);
}
function movePaint(e) {
  if (Math.abs(e.clientY - paint.y) > 4) paint.moved = true;
  const at = minutesAt(paint.col, e.clientY);
  paint.start = Math.min(at, paint.anchor);
  paint.end = at >= paint.anchor ? Math.max(at, paint.anchor + SNAP) : paint.anchor;
  placeBlock(paint.block, paint.col, paint.start, paint.end);
}
async function endPaint() {
  const p = paint;
  paint = null;
  swallowNextClick();
  p.block.remove();
  if (!p.moved) return openEditor({ date: p.col.dataset.date, startMinutes: p.anchor, unavailable: true });
  const event = {
    id: newEventId(), title: "Can't train", kind: "unavailable",
    start: toHHMM(p.start), end: toHHMM(p.end), repeat: "once", date: p.col.dataset.date,
  };
  myEvents = [...myEvents, event];
  saveList(EVENTS_KEY, myEvents);
  await withLoading(() => replan());
}

// Click empty time (Plan mode) to add a commitment there, snapped to 30 minutes.
planRegion.addEventListener("click", (e) => {
  const col = e.target.closest(".day-col");
  if (!col || e.target !== col || mode !== "plan") return;
  const rect = col.getBoundingClientRect();
  const [first, last] = colRange(col);
  const minutes = first + ((e.clientY - rect.top) / rect.height) * (last - first);
  openEditor({ date: col.dataset.date, startMinutes: Math.floor(minutes / 30) * 30 });
});

// ------------------------------------------------------------------ chat ---
function addMessage(text, who, imageUrl) {
  const row = document.createElement("div");
  row.className = `msg ${who === "user" ? "user-msg" : "coach-msg"}`;
  const bubble = document.createElement("p");
  if (imageUrl) {
    const img = document.createElement("img");
    img.src = imageUrl;
    img.alt = "Schedule screenshot";
    img.className = "msg-image";
    bubble.appendChild(img);
  }
  if (text) bubble.appendChild(document.createTextNode(text)); // text node, never innerHTML
  row.appendChild(bubble);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

async function withTyping(text, request) {
  const typing = addMessage(text, "coach");
  typing.classList.add("typing");
  try {
    const data = await request();
    typing.remove();
    if (data && data.reply) addMessage(data.reply, "coach");
    return data;
  } catch (err) {
    typing.remove();
    addMessage(err.message, "coach");
    return null;
  }
}

// Screenshot attachments
const imageInput = document.getElementById("image-input");
const attachment = document.getElementById("attachment");
const attachmentThumb = document.getElementById("attachment-thumb");
let pendingImage = null; // { data: dataURL, type }

function shrinkImage(file) {
  // Resize to at most 2000px so uploads stay small but text stays readable.
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, 2000 / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve({ data: canvas.toDataURL("image/jpeg", 0.9), type: "image/jpeg" });
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => reject(new Error("That file isn't an image I can read."));
    img.src = URL.createObjectURL(file);
  });
}

imageInput.addEventListener("change", async () => {
  const file = imageInput.files[0];
  imageInput.value = "";
  if (!file) return;
  try {
    pendingImage = await shrinkImage(file);
    attachmentThumb.src = pendingImage.data;
    document.getElementById("attachment-name").textContent = file.name;
    attachment.hidden = false;
    chatInput.placeholder = "Add a note (optional) and press send";
    chatInput.focus();
  } catch (err) {
    addMessage(err.message, "coach");
  }
});
document.getElementById("attachment-remove").addEventListener("click", () => {
  pendingImage = null;
  attachment.hidden = true;
  chatInput.placeholder = "Ask to change your plan…";
});

// Near the limit, show a count; over it, say so plainly and keep the text so it can be shortened.
function updateLimit() {
  const length = chatInput.value.trim().length;
  const over = length > MAX_MESSAGE;
  chatLimit.hidden = length < MAX_MESSAGE * 0.9;
  chatLimit.classList.toggle("over", over);
  chatLimit.textContent = `${length.toLocaleString()} / ${MAX_MESSAGE.toLocaleString()} characters` +
    (over ? ". Shorten your message to send it." : "");
  return over;
}

async function send(text) {
  text = text.trim();
  const image = pendingImage;
  if ((!text && !image) || sendButton.disabled) return;
  if (updateLimit()) {
    chatInput.focus();
    return;
  }
  addMessage(text, "user", image && image.data);
  chatInput.value = "";
  autoGrow();
  updateLimit();
  pendingImage = null;
  attachment.hidden = true;
  chatInput.placeholder = "Ask to change your plan…";
  sendButton.disabled = true;
  await withTyping(image ? "Reading your schedule" : "Thinking", () =>
    post("/api/chat", image ? { message: text, image } : { message: text })
  );
  sendButton.disabled = false;
  chatInput.focus();
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  send(chatInput.value);
});
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send(chatInput.value);
  }
});
function autoGrow() {
  chatInput.style.height = "auto";
  chatInput.style.height = `${chatInput.scrollHeight}px`;
}
chatInput.addEventListener("input", () => {
  autoGrow();
  updateLimit();
});
document.getElementById("chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip && !chip.dataset.action) send(chip.textContent);
});

// ------------------------------------------------------------ Preferences ---
// Choices are a draft: the calendar only changes when you press Apply changes.
const prefsPanel = document.getElementById("prefs");
const prefsBackdrop = document.getElementById("prefs-backdrop");
const prefsForm = document.getElementById("prefs-form");
const prefsApply = document.getElementById("prefs-apply");
const prefsStatus = document.getElementById("prefs-status");
const prefsError = document.getElementById("prefs-error");
const calendarList = document.getElementById("calendar-list");
const PREF_NAMES = {
  commitment: "commitment", session_minutes: "workout length", preferred_time: "preferred time",
  buffer_minutes: "travel buffer", split: "split", split_scope: "split", google_excluded: "calendars",
};
let applying = false;

function setRadio(name, value) {
  for (const input of prefsForm.querySelectorAll(`input[name="${name}"]`)) input.checked = input.value === String(value);
}

function fillPrefs() {
  for (const name of ["commitment", "session_minutes", "preferred_time", "buffer_minutes", "split", "split_scope"]) {
    setRadio(name, appliedPrefs[name]);
  }
  calendarList?.querySelectorAll("input[data-key]").forEach((box) => {
    box.checked = !(appliedPrefs.google_excluded || []).includes(box.dataset.key);
  });
  const own = myEvents.filter((ev) => !isSampleEvent(ev)).length;
  const sample = myEvents.length - own;
  document.getElementById("own-count").textContent = sample
    ? `Sample week loaded (${sample} commitments)`
    : `${own} added here`;
  prefsError.hidden = true;
  updateDraft();
}

function draftPrefs() {
  const value = (name) => prefsForm.querySelector(`input[name="${name}"]:checked`)?.value;
  const draft = {
    commitment: value("commitment"),
    session_minutes: Number(value("session_minutes")),
    preferred_time: value("preferred_time"),
    buffer_minutes: Number(value("buffer_minutes")),
    split: value("split"),
    split_scope: value("split") === "recommended" ? appliedPrefs.split_scope : value("split_scope"),
  };
  if (calendarList && calendarList.querySelector("input[data-key]")) {
    draft.google_excluded = [...calendarList.querySelectorAll("input[data-key]:not(:checked)")].map((b) => b.dataset.key);
  }
  return draft;
}

function changedPrefs(draft) {
  return Object.keys(draft).filter((key) => {
    if (key === "google_excluded") {
      const before = [...(appliedPrefs.google_excluded || [])].sort().join();
      return [...draft[key]].sort().join() !== before;
    }
    if (key === "split_scope" && draft.split === "recommended") return false;
    return String(draft[key]) !== String(appliedPrefs[key]);
  });
}

function updateDraft() {
  const draft = draftPrefs();
  const changed = [...new Set(changedPrefs(draft).map((k) => PREF_NAMES[k]))];
  document.getElementById("split-scope").hidden = draft.split === "recommended";
  prefsApply.disabled = applying || !changed.length;
  prefsStatus.textContent = applying ? "Updating your plan…" : changed.length ? `Changed: ${changed.join(", ")}` : "No changes yet";
}
prefsForm.addEventListener("change", () => {
  prefsError.hidden = true;
  updateDraft();
});

async function loadCalendars() {
  if (!calendarList) return;
  try {
    const res = await fetch("/google/calendars");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Couldn't load your calendars.");
    calendarList.innerHTML = "";
    for (const cal of data.calendars) {
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.dataset.key = cal.key;
      box.checked = cal.included;
      const name = document.createElement("span");
      name.textContent = `${cal.name}${cal.primary ? " (main)" : ""}`;
      const src = document.createElement("small");
      src.textContent = "Google";
      label.append(box, name, src);
      calendarList.appendChild(label);
    }
    if (!data.calendars.length) calendarList.textContent = "No calendars found.";
  } catch (err) {
    calendarList.textContent = err.message;
  }
  fillPrefs();
}

function openPreferences() {
  closePopover();
  toggleMenu(false);
  fillPrefs();
  prefsPanel.hidden = prefsBackdrop.hidden = false;
  document.querySelector('[data-action="open-preferences"]')?.setAttribute("aria-expanded", "true");
  if (calendarList && !calendarList.dataset.loaded) {
    calendarList.dataset.loaded = "true";
    loadCalendars();
  }
  prefsPanel.querySelector(".icon-btn").focus();
}

// Closing without applying (Cancel, the X, Escape or clicking outside) throws the draft away.
function closePreferences() {
  if (applying) return;
  prefsPanel.hidden = prefsBackdrop.hidden = true;
  document.querySelector('[data-action="open-preferences"]')?.setAttribute("aria-expanded", "false");
  fillPrefs();
}
prefsBackdrop.addEventListener("click", closePreferences);

prefsApply.addEventListener("click", async () => {
  const draft = draftPrefs();
  if (applying || !changedPrefs(draft).length) return; // one update at a time
  applying = true;
  prefsApply.textContent = "Applying…";
  updateDraft();
  try {
    await withLoading(() => post("/api/preferences", { preferences: draft }));
    applying = false;
    closePreferences();
    showToast("Preferences updated");
  } catch (err) {
    prefsError.textContent = `${err.message} Your previous settings are still in place.`;
    prefsError.hidden = false;
  } finally {
    applying = false;
    prefsApply.textContent = "Apply changes";
    updateDraft();
  }
});

// ------------------------------------------------------------ sample weeks ---
// A sample week replaces your commitments while you explore; yours are kept aside and come back.
async function loadSample(name) {
  if (!hasStored(SAVED_EVENTS_KEY)) saveList(SAVED_EVENTS_KEY, myEvents.filter((ev) => !isSampleEvent(ev)));
  closePreferences();
  const data = await withLoading(() => replan({ load_sample: name }));
  addMessage(`Here's a sample week to explore; your own commitments are kept aside until you switch back. ${data.intro || ""}`.trim(), "coach");
}
async function clearSample() {
  myEvents = loadList(SAVED_EVENTS_KEY).filter((ev) => !isSampleEvent(ev));
  saveList(SAVED_EVENTS_KEY, null);
  saveList(EVENTS_KEY, myEvents);
  const data = await withLoading(() => replan());
  addMessage(`Back to your own schedule. ${data.intro || ""}`.trim(), "coach");
}

// ---------------------------------------------------------- Google sync ---
const syncDialog = document.getElementById("sync-dialog");
const syncWeeks = document.getElementById("sync-weeks");
const syncMessage = document.getElementById("sync-message");
const syncConfirm = document.getElementById("sync-confirm");
let syncPreview = [];

function showSyncMessage(text) {
  syncMessage.textContent = text || "";
  syncMessage.hidden = !text;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function renderSync(weeks) {
  syncPreview = weeks;
  syncWeeks.innerHTML = "";
  for (const week of weeks) {
    const group = el("section", "sync-week");
    const head = el("label", "sync-week-head");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.dataset.week = week.week;
    box.checked = week.changes.length > 0;
    box.disabled = !week.changes.length;
    head.append(box, el("b", "", `Week of ${week.label}`), el("span", "muted", week.selected ? "on screen" : "has changes"));
    group.appendChild(head);
    const list = el("ul", "sync-list");
    for (const change of week.changes) {
      const li = el("li");
      li.appendChild(el("span", `kind ${change.kind}`, change.kind));
      const text = el(change.kind === "restore" ? "label" : "span");
      if (change.kind === "restore") {
        const restore = document.createElement("input");
        restore.type = "checkbox";
        restore.dataset.restore = change.key; // off by default: deleted workouts stay deleted unless chosen
        text.append(restore, " ");
      }
      text.append(change.label.replace(/^(Add|Move|Update|Remove|Restore) /, "")); // the badge already says it
      li.appendChild(text);
      list.appendChild(li);
    }
    for (const label of week.kept) {
      const li = el("li");
      li.append(el("span", "kind kept", "kept"), el("span", "", label));
      list.appendChild(li);
    }
    if (!week.changes.length) list.appendChild(el("li", "muted", "Already in sync."));
    else if (week.unchanged) list.appendChild(el("li", "muted", `${week.unchanged} workout${week.unchanged === 1 ? "" : "s"} already up to date`));
    group.appendChild(list);
    syncWeeks.appendChild(group);
  }
  updateSyncButton();
}
function updateSyncButton() {
  syncConfirm.disabled = !syncWeeks.querySelector("input[data-week]:checked");
}
syncWeeks.addEventListener("change", updateSyncButton);

async function openSync() {
  closePreferences();
  syncWeeks.innerHTML = "<p class='muted'>Checking your Google Calendar…</p>";
  showSyncMessage("");
  syncConfirm.disabled = true;
  syncDialog.showModal();
  try {
    renderSync((await post("/google/sync/preview", {})).weeks);
  } catch (err) {
    syncWeeks.innerHTML = "";
    showSyncMessage(err.message);
  }
}

syncConfirm.addEventListener("click", async () => {
  syncConfirm.disabled = true;
  const weeks = [...syncWeeks.querySelectorAll("input[data-week]:checked")].map((box) => {
    const week = syncPreview.find((w) => w.week === box.dataset.week);
    const restore = [...box.closest(".sync-week").querySelectorAll("input[data-restore]:checked")].map((b) => b.dataset.restore);
    return { week: week.week, fingerprint: week.fingerprint, restore };
  });
  try {
    const data = await post("/google/sync/apply", { weeks });
    if (data.status === "changed") {
      renderSync(data.weeks);
      showSyncMessage("Your plan changed since this preview. Check the updated changes and confirm again.");
      return;
    }
    syncDialog.close();
    addMessage(data.reply, "coach");
  } catch (err) {
    showSyncMessage(err.message);
    updateSyncButton();
  }
});
syncDialog.addEventListener("click", (e) => {
  if (e.target === syncDialog || e.target.closest("[data-close]")) syncDialog.close();
});

// ------------------------------------------------- buttons anywhere on the page ---
document.addEventListener("click", async (e) => {
  const target = e.target.closest("[data-action], [data-event-id], .workout");
  if (openPop && !e.target.closest(".workout-pop") && target !== openPop.block) closePopover();
  if (!menu.hidden && !e.target.closest(".menu-wrap")) toggleMenu(false);
  if (!target) return;
  const action = target.dataset.action;

  if (target.classList.contains("workout") && !action) {
    if (mode === "availability") return;
    return openPop?.block === target ? closePopover() : openPopover(target);
  }
  if (target.dataset.eventId && !action) return openEditor({ eventId: target.dataset.eventId });

  switch (action) {
    case "go-week":
      if (target.getAttribute("aria-disabled") === "true") return;
      return withLoading(() => replan({ week: target.dataset.week === "today" ? "" : target.dataset.week }));
    case "mode":
      mode = target.dataset.mode;
      closePopover();
      return applyMode();
    case "add-event":
      closePreferences();
      return openEditor({});
    case "close-pop":
      return closePopover();
    case "move-to":
      return moveWorkout(openPop.block, target.dataset.start);
    case "undo":
      if (undoMoves) {
        myMoves = undoMoves;
        saveList(MOVES_KEY, myMoves);
        hideUndo();
        await withLoading(() => replan());
      }
      return;
    case "skip":
      closePopover();
      return withTyping("Updating your week", () => post("/api/chat", { action: "skip", date: target.dataset.date }));
    case "proposal":
      return withTyping(
        target.dataset.accept === "true" ? "Applying the change" : "Keeping your plan",
        () => post("/api/proposal", { token: target.dataset.token, accept: target.dataset.accept === "true" })
      );
    case "open-preferences":
      return prefsPanel.hidden ? openPreferences() : closePreferences();
    case "close-preferences":
      return closePreferences();
    case "toggle-menu":
      return toggleMenu();
    case "screenshot":
      toggleMenu(false);
      return imageInput.click();
    case "load-sample":
      return loadSample(target.dataset.sample);
    case "clear-sample":
      return clearSample();
    case "clear-events":
      toggleMenu(false);
      if (myEvents.length && (await ask(`Delete all ${myEvents.length} of your commitments? This can't be undone.`))) {
        myEvents = [];
        saveList(EVENTS_KEY, myEvents);
        await withLoading(() => replan());
      }
      return;
    case "reset-preferences":
      toggleMenu(false);
      if (await ask("Reset all preferences to the defaults? Your commitments and moved workouts stay.")) {
        await withLoading(() => post("/api/preferences", { reset: true }));
        showToast("Preferences reset");
      }
      return;
    case "sync-preview":
      return openSync();
    case "reset-split":
      return withTyping("Updating your split", () => post("/api/chat", { action: "reset_split" }));
    case "replan":
      toggleMenu(false);
      return withTyping("Re-planning your week", () => post("/api/chat", { action: "replan" }));
  }
});

// Download .ics: the server needs your commitments and moves to plan the same week.
document.querySelector(".export-form").addEventListener("submit", (e) => {
  const form = e.target;
  form.events.value = JSON.stringify(myEvents);
  form.moves.value = JSON.stringify(myMoves);
  form.week.value = currentWeek;
  toggleMenu(false);
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (openPop) closePopover();
  else if (!prefsPanel.hidden) closePreferences();
  else if (!menu.hidden) toggleMenu(false);
});

// ------------------------------------------------------------------ start ---
// The server rendered the page without your data (it lives here), so re-plan with it.
// On your very first visit, the busy student sample week is loaded to explore.
withLoading(() => replan(firstVisit ? { load_sample: "busy" } : {}))
  .then((data) => {
    const intro = document.getElementById("coach-intro");
    if (intro && data.intro) intro.textContent = data.intro;
  })
  .catch(() => {});
