/* PTP web app. Plain JavaScript, no build step, no external files: works with the network off. */
'use strict';

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const MONTHS = ['Enero', 'Pebrero', 'Marso', 'Abril', 'Mayo', 'Hunyo', 'Hulyo', 'Agosto', 'Setyembre', 'Oktubre', 'Nobyembre', 'Disyembre'];
const MONTHS_SHORT = ['Ene', 'Peb', 'Mar', 'Abr', 'May', 'Hun', 'Hul', 'Ago', 'Set', 'Okt', 'Nob', 'Dis'];
const WEEKDAYS = ['Linggo', 'Lunes', 'Martes', 'Miyerkules', 'Huwebes', 'Biyernes', 'Sabado'];
const WD_SHORT = ['Lin', 'Lun', 'Mar', 'Miy', 'Huw', 'Biy', 'Sab'];
const KIND_COLORS = { rhu: '#2a5db0', midwife: '#8a4fb3', hospital: '#b3261e' };
const KIND_LABELS = { rhu: 'Health center', midwife: 'Midwife clinic', hospital: 'Ospital' };

const state = {
  health: null,
  month: null,                       // {year, month}
  chat: [],
  nearby: { kind: 'all', loc: null }, // loc = {lat, lon} from the device, or null for the demo location
  notes: { stage: 'input', consent: false, transcript: '', visitDate: '', result: null, picked: new Set(), pickedQ: new Set(),
           recording: false, seconds: 0, message: '' },
};

// ------------------------------------------------------------------ helpers
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const err = new Error(data.error || 'Hindi gumana. Subukan ulit.');
    err.code = data.code; err.status = res.status;
    throw err;
  }
  return data;
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function parseISO(iso) { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); }
function fmtDay(iso) { const d = parseISO(iso); return `${WEEKDAYS[d.getDay()]}, ${MONTHS_SHORT[d.getMonth()]} ${d.getDate()}`; }
function fmtShort(iso) { const d = parseISO(iso); return `${MONTHS_SHORT[d.getMonth()]} ${d.getDate()}`; }
function fmtLong(iso) { const d = parseISO(iso); return `${MONTHS_SHORT[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`; }
function fmtTime(t) {
  if (!t) return '';
  const [h, m] = t.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'NU' : 'NH'}`; // NU = nang umaga, NH = nang hapon
}
function dateChip(t) {
  if (!t.date) return '';
  const cls = t.kind === 'appointment' ? 'appt' : 'date';
  return `<span class="chip ${cls}">${esc(fmtShort(t.date))}${t.time ? ' · ' + esc(fmtTime(t.time)) : ''}</span>`;
}
function toast(msg) {
  const el = $('#toast'); el.textContent = msg; el.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('show'), 5000);
}
const icon = {
  alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l10 18H2z"/><line x1="12" y1="10" x2="12" y2="14"/><line x1="12" y1="17.5" x2="12" y2="17.6"/></svg>',
  cal: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/></svg>',
  phone: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/></svg>',
  mic: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><line x1="12" y1="18" x2="12" y2="22"/></svg>',
  left: '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="15 5 8 12 15 19"/></svg>',
  right: '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 5 16 12 9 19"/></svg>',
  send: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="13 6 19 12 13 18"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 12 10 18 20 6"/></svg>',
};

// ------------------------------------------------------------------ banner
function renderBanner() {
  const h = state.health, el = $('#banner');
  if (!h) { el.innerHTML = ''; return; }
  const lines = [];
  if (h.mode === 'mock') lines.push('TEST MODE: a rule-based stand-in is answering, not an AI model.');
  else if (!h.llm_ready) lines.push('<span class="bad">Hindi mahanap ang local AI model (Ollama).</span> Gumagana pa rin ang kalendaryo, mapa at Emergency.');
  el.innerHTML = lines.join('<br>');
}

// ------------------------------------------------------------------ router
const routes = { home: viewHome, calendar: viewCalendar, notes: viewNotes, ask: viewAsk, nearby: viewNearby, emergency: viewEmergency, summary: viewSummary };

async function render() {
  const name = (location.hash.replace(/^#\/?/, '') || 'home').split('?')[0];
  const view = routes[name] ? name : 'home';
  const tabs = $('#tabs');
  tabs.classList.toggle('hidden', view === 'emergency');
  tabs.querySelectorAll('a').forEach((a) => a.classList.toggle('active', a.dataset.tab === view));
  const el = $('#view');
  el.classList.toggle('no-tabs', view === 'emergency');
  try {
    await routes[view](el);
  } catch (err) {
    el.innerHTML = `<div class="note-red">${esc(err.message)}</div>`;
  }
}
window.addEventListener('hashchange', () => { render(); $('#view').scrollTop = 0; });

// ------------------------------------------------------------------ home
function setupForm() {
  return `
    <h1 class="page-title">Kumusta!</h1>
    <p class="h-sub">Ilagay ang petsa ng huling regla (LMP) para makuwenta ang linggo at due date. Nasa telepono mo lang ang lahat ng ito.</p>
    <form class="card" data-form="profile">
      <label class="field">Pangalan (opsyonal)<input type="text" name="name" autocomplete="off"></label>
      <label class="field">Unang araw ng huling regla<input type="date" name="lmp" required max="${esc(state.health?.today || '')}"></label>
      <button class="btn" type="submit">I-save</button>
    </form>`;
}

async function viewHome(el) {
  const h = await api('/api/home');
  if (!h.profile) { el.innerHTML = setupForm(); return; }
  const p = h.profile;
  const next = h.next_appointment;
  const tasks = h.tasks.map((t) => `
    <div class="row">
      <button class="box ${t.done ? 'on' : ''}" data-action="toggle" data-id="${esc(t.id)}" aria-label="Markahan: ${esc(t.title)}">${t.done ? icon.check : ''}</button>
      <span class="grow task-title ${t.done ? 'done' : ''}">${esc(t.title)}</span>${dateChip(t)}
    </div>`).join('');
  el.innerHTML = `
    <div class="row between">
      <div><div class="muted" style="font-size:14px">Magandang araw,</div><h1 class="page-title">${esc(p.name || 'Mommy')}</h1></div>
      <span class="chip"><span class="dot"></span>&nbsp;Sa device lang</span>
    </div>
    <section class="hero" aria-label="Linggo ng pagbubuntis">
      <div class="row between" style="align-items:flex-end">
        <div class="row" style="align-items:baseline;gap:8px"><span class="big">${p.weeks}</span><span>ng 40 linggo${p.days ? ` · +${p.days} araw` : ''}</span></div>
        <span class="chip">Trimester ${p.trimester}</span>
      </div>
      <div class="bar"><i style="width:${Math.round(p.progress * 100)}%"></i></div>
      <div class="row between small" style="font-size:13px"><span>Tinatayang due date</span><strong>${esc(fmtLong(p.due_date))}</strong></div>
      <div style="font-size:11px;opacity:.9">Tantiya lang, mula sa petsa ng huling regla.</div>
    </section>
    <a class="card row" href="#/calendar" style="flex-direction:row;text-decoration:none">
      <div class="iconbox">${icon.cal}</div>
      <div class="grow">
        <div class="muted small" style="font-weight:600">Susunod na check-up</div>
        ${next ? `<div style="font-size:16px;font-weight:700">${esc(fmtDay(next.date))}${next.time ? ' · ' + esc(fmtTime(next.time)) : ''}</div><div class="muted" style="font-size:13px">${esc(next.title)}</div>`
               : '<div class="empty">Wala pa. Mag-record ng konsulta para madagdag.</div>'}
      </div>
    </a>
    <section class="card">
      <div class="row between"><h2 style="font-size:16px">Mga gagawin</h2><a href="#/summary" style="font-size:13px;font-weight:700;color:var(--primary);text-decoration:none">Buod para sa midwife</a></div>
      ${tasks || '<div class="empty">Walang nakatala. Pindutin ang Tala para mag-record ng konsulta.</div>'}
    </section>
    <a class="btn red" href="#/emergency">${icon.alert} Emergency</a>`;
}

// ------------------------------------------------------------------ calendar
async function viewCalendar(el) {
  if (!state.month) { const t = new Date((state.health?.today || new Date().toISOString().slice(0, 10)) + 'T00:00:00'); state.month = { year: t.getFullYear(), month: t.getMonth() + 1 }; }
  const c = await api(`/api/calendar?year=${state.month.year}&month=${state.month.month}`);
  const cells = Array.from({ length: c.lead_blanks }, () => '<div class="cell"></div>').join('') + c.days.map((d) => {
    const kinds = new Set(d.items.map((i) => i.kind));
    const pip = kinds.has('appointment') ? 'appointment' : (d.items.length ? 'task' : '');
    const cls = d.today ? 'today' : (d.due ? 'due' : '');
    return `<div class="cell ${cls}"><div class="num">${d.n}</div><div class="pip ${pip}"></div></div>`;
  }).join('');
  const p = c.profile;
  const agenda = c.agenda.map((t) => `<div class="row"><span class="dot" style="background:${t.kind === 'appointment' ? '#c4472a' : 'var(--primary)'}"></span><span class="grow" style="font-size:14px">${esc(t.title)}</span><span class="muted" style="font-size:13px">${esc(fmtShort(t.date))}${t.time ? ' · ' + esc(fmtTime(t.time)) : ''}</span></div>`).join('');
  el.innerHTML = `
    <div class="row between"><h1 class="page-title">Kalendaryo</h1>${p ? `<span class="chip appt">Due: ${esc(fmtLong(p.due_date))}</span>` : ''}</div>
    <section class="card" style="padding:14px 12px 8px">
      <div class="row between" style="padding:0 4px 6px">
        <button class="navbtn" data-action="month" data-delta="-1" aria-label="Nakaraang buwan">${icon.left}</button>
        <strong style="font-size:20px">${MONTHS[c.month - 1]} ${c.year}</strong>
        <button class="navbtn" data-action="month" data-delta="1" aria-label="Susunod na buwan">${icon.right}</button>
      </div>
      <div class="cal">${WD_SHORT.map((w) => `<div class="wd">${w}</div>`).join('')}${cells}</div>
    </section>
    ${p ? `<section class="card">
      <h2 style="font-size:15px">Daan ng pagbubuntis</h2>
      <div class="tl" role="img" aria-label="Linggo ${p.weeks} sa 40">
        <i style="left:0;width:32.5%;background:var(--t1);border-radius:5px 0 0 5px"></i>
        <i style="left:32.5%;width:35%;background:var(--t2)"></i>
        <i style="left:67.5%;width:32.5%;background:var(--t3);border-radius:0 5px 5px 0"></i>
        <i class="mark" style="left:${Math.round(p.progress * 1000) / 10}%"></i>
      </div>
      <div class="row between muted small"><span>Trimester 1</span><span>Trimester 2</span><span>Trimester 3</span></div>
      <div class="muted small">Nasa linggo ${p.weeks} ka ngayon. Tantiya lang ang due date, hindi eksaktong araw ng panganganak.</div>
    </section>` : ''}
    <section class="card"><h2 style="font-size:15px">Susunod</h2>${agenda || '<div class="empty">Wala pang nakatakda.</div>'}</section>`;
}

// ------------------------------------------------------------------ visit notes
let recorder = null, recChunks = [], recTimer = null;
const N = () => state.notes;

async function viewNotes(el) {
  if (!N().visitDate) N().visitDate = state.health?.today || new Date().toISOString().slice(0, 10);
  renderNotes(el);
}

function renderNotes(el = $('#view')) {
  if (location.hash.replace(/^#\/?/, '') !== 'notes') return;
  const n = N(), h = state.health || {};
  let body = '';
  if (n.stage === 'working') {
    body = `<div class="card recbox"><div class="spinner"></div><div>${esc(n.message || 'Sandali lang…')}</div><div class="muted small">Tumatakbo sa device mo. Walang internet na ginagamit.</div></div>`;
  } else if (n.stage === 'saved') {
    body = `<div class="card recbox"><div class="iconbox" style="width:56px;height:56px">${icon.cal}</div><strong style="font-size:18px">Naidagdag na sa kalendaryo</strong>
      <a class="btn" href="#/calendar">Tingnan ang kalendaryo</a><button class="btn secondary" data-action="notes-reset">Bagong tala</button></div>`;
  } else if (n.stage === 'review' && n.result) {
    body = reviewHTML(n);
  } else {
    const canRecord = !!(navigator.mediaDevices && window.MediaRecorder) && !!h.stt;
    const why = !h.stt ? 'Walang naka-install na local speech model. I-paste na lang ang transcript sa ibaba.'
      : (!(navigator.mediaDevices && window.MediaRecorder) ? 'Hindi sinusuportahan ng browser na ito ang pag-record dito. Gamitin ang laptop sa localhost, o i-paste ang transcript.' : '');
    body = `
      <label class="card check"><input type="checkbox" data-action="consent" ${n.consent ? 'checked' : ''}><span style="font-size:14px;line-height:1.4">Nagpaalam na ako sa doktor o midwife bago mag-record.</span></label>
      <section class="card recbox" aria-label="Record">
        ${n.recording
          ? `<span class="chip red"><span class="dot" style="background:var(--red)"></span>&nbsp;Nagre-record</span>
             <div class="timer" id="timer">${fmtSecs(n.seconds)}</div>
             <div class="wave" aria-hidden="true">${Array.from({ length: 24 }, (_, i) => `<i style="height:${18 + ((i * 37) % 42)}px;animation-delay:${(i % 8) * 0.11}s"></i>`).join('')}</div>
             <button class="recbtn" data-action="rec-stop" aria-label="Itigil ang record"><i></i></button>`
          : `<button class="recbtn start" data-action="rec-start" aria-label="Simulan ang record" ${canRecord && n.consent ? '' : 'disabled style="opacity:.5;cursor:not-allowed"'}>${icon.mic}</button>
             <div class="muted small">${n.consent ? (why || 'Pindutin para mag-record.') : 'I-check muna ang pahintulot sa itaas.'}</div>`}
        <div class="muted small">Naka-save lang sa device ang boses.</div>
      </section>
      <label class="field">Transcript (puwedeng i-edit o i-paste)
        <textarea id="transcript" placeholder="Midwife: …&#10;Maria: …">${esc(n.transcript)}</textarea></label>
      <label class="field">Petsa ng konsulta<input type="date" id="visitDate" value="${esc(n.visitDate)}" max="${esc(h.today || '')}"></label>
      <div class="row"><button class="btn secondary small" data-action="sample">Gamitin ang sample na usapan</button></div>
      <button class="btn" data-action="analyze">Gawing tala at mga gagawin</button>`;
  }
  el.innerHTML = `<div><h1 class="page-title">Tala ng Konsulta</h1><p class="h-sub">I-record o i-paste ang usapan. Ang AI ay nagmumungkahi lang; ikaw ang magkukumpirma.</p></div>${body}`;
}

function reviewHTML(n) {
  const r = n.result;
  const tasks = r.tasks.map((t, i) => `
    <label class="card task check" style="flex-direction:row;gap:12px">
      <input type="checkbox" data-action="pick" data-i="${i}" ${n.picked.has(i) ? 'checked' : ''}>
      <div class="grow" style="display:flex;flex-direction:column;gap:5px">
        <div class="row between"><strong style="font-size:15px">${esc(t.title)}</strong>${dateChip(t)}</div>
        <div class="quote">“${esc(t.quote)}”</div>
        ${t.verify ? '<span class="chip warn" style="align-self:flex-start">I-verify sa doktor o midwife</span>' : ''}
      </div>
    </label>`).join('');
  const qs = r.unanswered.map((q, i) => `
    <label class="check" style="font-size:14px"><input type="checkbox" data-action="pickq" data-i="${i}" ${n.pickedQ.has(i) ? 'checked' : ''}><span>${esc(q.question)}</span></label>`).join('');
  return `
    ${r.emergency.length ? `<div class="note-red">May nabanggit na babalang senyales sa usapan (${esc(r.emergency.join(', '))}). Kung nangyayari ito ngayon, buksan ang <a href="#/emergency">Emergency</a>.</div>` : ''}
    ${r.model === 'mock' ? '<div class="note-warn">TEST MODE: ang mga task ay galing sa simpleng rules, hindi sa AI model.</div>' : ''}
    ${r.summary ? `<section class="card"><strong class="small" style="color:var(--primary)">Buod (isinulat ng AI, tingnan ang transcript)</strong><div style="font-size:14px;line-height:1.45">${esc(r.summary)}</div></section>` : ''}
    <div class="muted small" style="font-weight:700">Mga gagawin · kumpirmahin bago idagdag</div>
    ${tasks || '<div class="empty">Walang nakitang task na may tugmang sipi sa transcript.</div>'}
    ${r.rejected ? `<div class="muted small">${r.rejected} mungkahi ang itinapon dahil walang tugmang sipi sa transcript.</div>` : ''}
    ${qs ? `<section class="card"><strong class="small">Mga tanong na hindi nasagot (idagdag sa listahan para sa susunod na check-up)</strong>${qs}</section>` : ''}
    <button class="btn" data-action="save-tasks" ${n.picked.size || n.pickedQ.size ? '' : 'disabled'}>Idagdag sa kalendaryo</button>
    <button class="btn secondary" data-action="notes-back">Bumalik at i-edit ang transcript</button>
    <p class="muted small" style="text-align:center">Maaaring may mali ang pagkilala sa boses. Hindi ito kapalit ng payo ng doktor o midwife.</p>`;
}

const fmtSecs = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream); recChunks = [];
    recorder.ondataavailable = (e) => { if (e.data.size) recChunks.push(e.data); };
    recorder.onstop = onRecordingStopped;
    recorder.start();
    N().recording = true; N().seconds = 0;
    recTimer = setInterval(() => { N().seconds += 1; const t = $('#timer'); if (t) t.textContent = fmtSecs(N().seconds); }, 1000);
    renderNotes();
  } catch (err) {
    toast('Hindi ma-access ang mikropono: ' + err.message);
  }
}

function stopRecording() {
  if (!recorder) return;
  recorder.stop();
  recorder.stream.getTracks().forEach((t) => t.stop());
  clearInterval(recTimer);
  N().recording = false;
}

async function onRecordingStopped() {
  const type = recorder.mimeType || 'audio/webm';
  const blob = new Blob(recChunks, { type });
  const ext = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'mp4' : 'webm';
  N().stage = 'working'; N().message = 'Ginagawang teksto ang boses…'; renderNotes();
  try {
    const r = await api('/api/transcribe', { method: 'POST', headers: { 'X-Audio-Ext': ext }, body: blob });
    N().transcript = r.transcript;
    toast('Tapos na. I-check at i-edit ang transcript bago ituloy.');
  } catch (err) {
    toast(err.message);
  }
  N().stage = 'input'; renderNotes();
}

async function analyze() {
  const n = N();
  n.transcript = ($('#transcript')?.value ?? n.transcript).trim();
  n.visitDate = $('#visitDate')?.value || n.visitDate;
  if (!n.transcript) { toast('Maglagay muna ng transcript.'); return; }
  n.stage = 'working'; n.message = 'Hinahanap ang mga gagawin at petsa…'; renderNotes();
  try {
    n.result = await post('/api/extract', { transcript: n.transcript, visit_date: n.visitDate });
    n.picked = new Set(n.result.tasks.map((_, i) => i));
    n.pickedQ = new Set();
    n.stage = 'review';
  } catch (err) {
    toast(err.code === 'llm_unavailable' ? 'Hindi nakakonekta ang local AI model. Tingnan ang README (Ollama).' : err.message);
    n.stage = 'input';
  }
  renderNotes();
}

async function saveTasks() {
  const n = N(), r = n.result;
  const tasks = r.tasks.filter((_, i) => n.picked.has(i));
  const questions = r.unanswered.filter((_, i) => n.pickedQ.has(i)).map((q) => q.question);
  try {
    await post('/api/tasks', { tasks: tasks.length ? tasks : [], visit_date: n.visitDate, summary: r.summary, questions });
    n.stage = 'saved';
  } catch (err) {
    toast(err.message);
  }
  renderNotes();
}

// ------------------------------------------------------------------ ask
async function viewAsk(el) {
  const h = state.health || {};
  const log = state.chat.map((m) => {
    if (m.role === 'me') return `<div class="bubble me">${esc(m.text)}</div>`;
    const cls = m.kind === 'emergency' ? 'red' : (m.kind === 'answer' ? '' : 'amber');
    const cites = (m.citations || []).map((c) => `<span class="src">[${c.n}] ${esc(c.title)}${c.sample ? ' · sample, hindi opisyal' : ''}</span>`).join('');
    return `<div class="bubble bot ${cls}"><div>${esc(m.text)}</div>${cites}
      ${m.kind === 'emergency' ? '<a class="btn red small" href="#/emergency">Buksan ang Emergency</a>' : ''}
      ${m.added ? '<div class="small muted">Naidagdag sa mga tanong para sa check-up.</div>' : ''}
      ${m.kind === 'answer' ? '<div class="small muted">Hindi ito kapalit ng payo ng doktor o midwife.</div>' : ''}</div>`;
  }).join('');
  el.innerHTML = `
    <div><h1 class="page-title">Tanong kay PTP</h1>
      <div class="chip" style="margin-top:8px">Sumasagot mula sa mga gabay sa device</div>
      ${h.guides_are_sample ? '<div class="note-warn" style="margin-top:10px">Sample na gabay lang ang laman ngayon, hindi opisyal. Palitan bago gamitin sa totoo.</div>' : ''}</div>
    <div class="log" id="log">${log || '<div class="empty">Magtanong tungkol sa pagkain, check-up at iba pa. Hindi ako sasagot tungkol sa gamot o diagnosis.</div>'}</div>
    <form class="composer" data-form="ask"><input type="text" name="q" placeholder="Mag-type ng tanong…" autocomplete="off" aria-label="Tanong" required>
      <button class="send" type="submit" aria-label="Ipadala">${icon.send}</button></form>`;
  const box = $('#view'); box.scrollTop = box.scrollHeight;
}

async function ask(q) {
  state.chat.push({ role: 'me', text: q });
  state.chat.push({ role: 'bot', kind: 'wait', text: 'Hinahanap sa mga gabay…' });
  await viewAsk($('#view'));
  try {
    const r = await post('/api/ask', { question: q });
    state.chat[state.chat.length - 1] = { role: 'bot', kind: r.kind, text: r.answer, citations: r.citations, added: r.add_to_questions };
  } catch (err) {
    state.chat[state.chat.length - 1] = { role: 'bot', kind: 'error',
      text: err.code === 'llm_unavailable' ? 'Hindi nakakonekta ang local AI model. Tingnan ang README (Ollama).' : err.message };
  }
  await viewAsk($('#view'));
}

// ------------------------------------------------------------------ nearby
function mapSVG(loc, items) {
  const W = 350, H = 216, pad = 34;
  const lat0 = loc.lat, lon0 = loc.lon, k = Math.cos(lat0 * Math.PI / 180);
  const pts = items.map((f) => ({ f, x: (f.lon - lon0) * k, y: f.lat - lat0 }));
  const xs = pts.map((p) => p.x).concat(0), ys = pts.map((p) => p.y).concat(0);
  const spanX = Math.max(...xs) - Math.min(...xs) || 0.01, spanY = Math.max(...ys) - Math.min(...ys) || 0.01;
  const s = Math.min((W - 2 * pad) / spanX, (H - 2 * pad) / spanY);
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const X = (x) => W / 2 + (x - cx) * s, Y = (y) => H / 2 - (y - cy) * s;
  const pin = (x, y, color, label) => `<g transform="translate(${x.toFixed(1)},${y.toFixed(1)})"><path d="M0 0c-8-9-12-14-12-20a12 12 0 0 1 24 0c0 6-4 11-12 20z" fill="${color}"/><circle cx="0" cy="-20" r="4.5" fill="#fff"/><text x="0" y="14" text-anchor="middle" font-size="10.5" font-weight="700" fill="#1b2b2a" stroke="#e6efea" stroke-width="3" paint-order="stroke">${esc(label)}</text></g>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Payak na mapa ng mga pasilidad na malapit sa iyo"><rect width="${W}" height="${H}" fill="#e6efea"/>
    <path d="M0 ${H * 0.35} L${W} ${H * 0.3}" stroke="#fff" stroke-width="8" fill="none"/><path d="M${W * 0.3} 0 L${W * 0.34} ${H}" stroke="#fff" stroke-width="7" fill="none"/><path d="M0 ${H * 0.72} L${W} ${H * 0.78}" stroke="#fff" stroke-width="5" fill="none"/>
    <circle cx="${X(0)}" cy="${Y(0)}" r="20" fill="#17756e" fill-opacity=".18"/><circle cx="${X(0)}" cy="${Y(0)}" r="8" fill="#17756e" stroke="#fff" stroke-width="3"/>
    ${pts.map((p) => pin(X(p.x), Y(p.y), KIND_COLORS[p.f.kind] || '#555', p.f.name.length > 22 ? p.f.name.slice(0, 21) + '…' : p.f.name)).join('')}</svg>`;
}

async function viewNearby(el) {
  const loc = state.nearby.loc;
  const q = loc ? `?lat=${loc.lat}&lon=${loc.lon}` : '';
  const d = await api('/api/facilities' + q);
  const all = d.facilities;
  const shown = state.nearby.kind === 'all' ? all : all.filter((f) => f.kind === state.nearby.kind);
  const btn = (k, label) => `<button class="${state.nearby.kind === k ? 'on' : ''}" data-action="kind" data-kind="${k}">${label}</button>`;
  el.innerHTML = `
    <div><h1 class="page-title">Malapit sa akin</h1><p class="h-sub">${esc(d.location.name)}</p></div>
    <div class="filters">${btn('all', 'Lahat')}${btn('hospital', 'Ospital')}${btn('midwife', 'Midwife')}${btn('rhu', 'Health center')}</div>
    <div class="map">${mapSVG(d.location, shown)}<span class="tag">Offline na mapa (payak)</span></div>
    <button class="btn secondary small" data-action="locate">Gamitin ang lokasyon ng device</button>
    ${shown.map((f) => `<div class="facility"><span class="sw" style="background:${KIND_COLORS[f.kind] || '#555'}"></span>
       <div class="grow"><div style="font-size:15px;font-weight:700">${esc(f.name)}</div><div class="muted small">${esc(f.label || KIND_LABELS[f.kind] || '')}</div></div>
       <strong>${f.distance_km.toFixed(1)} km</strong></div>`).join('') || '<div class="empty">Walang nakalistang pasilidad.</div>'}
    <p class="muted small">Layo sa tuwid na linya, hindi sa kalsada. ${d.sample ? '<strong>Kathang-isip na mga pasilidad ito para sa halimbawa.</strong>' : ''}</p>`;
}

function locate() {
  if (!navigator.geolocation) { toast('Walang location sa browser na ito. Ginagamit ang halimbawang lokasyon.'); return; }
  navigator.geolocation.getCurrentPosition(
    (pos) => { state.nearby.loc = { lat: pos.coords.latitude, lon: pos.coords.longitude }; render(); },
    () => toast('Hindi makuha ang lokasyon (maaaring walang GPS o signal). Ginagamit ang halimbawang lokasyon.'),
    { timeout: 8000 });
}

// ------------------------------------------------------------------ emergency + summary
async function viewEmergency(el) {
  const [flags, near] = await Promise.all([api('/api/redflags'), api('/api/facilities' + (state.nearby.loc ? `?lat=${state.nearby.loc.lat}&lon=${state.nearby.loc.lon}` : ''))]);
  const first = near.facilities[0];
  el.innerHTML = `
    <div class="em-head">
      <a class="back" href="#/home">${icon.left} Bumalik</a>
      <h1>Emergency</h1>
      <p style="font-size:17px;line-height:1.4">Kung nasa panganib ka o ang sanggol, tumawag sa 911 o pumunta agad sa pinakamalapit na ospital.</p>
      <a class="em-call" href="tel:911">${icon.phone} Tumawag sa 911</a>
    </div>
    ${first ? `<div class="card row" style="flex-direction:row"><div class="iconbox">${icon.cal.replace(/<rect.*<\/svg>/, '<path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>')}</div>
      <div class="grow"><div class="muted small" style="font-weight:600">Pinakamalapit sa iyo</div><div style="font-size:16px;font-weight:700">${esc(first.name)}</div><div class="muted" style="font-size:13px">${first.distance_km.toFixed(1)} km sa tuwid na linya</div></div></div>` : ''}
    <section class="card">
      <h2 style="font-size:15px">Pumunta agad kung may alinman dito</h2>
      ${flags.signs.map((s) => `<div class="sign"><i></i><span>${esc(s.tl)} <span class="muted">· ${esc(s.en)}</span></span></div>`).join('')}
      ${flags.reviewed ? `<div class="muted small">Pinagmulan: ${esc(flags.source)}</div>` : '<div class="note-warn">HINDI PA NASURI ang listahang ito ng health worker. Placeholder lang. Palitan mula sa opisyal na gabay (DOH o WHO) bago gamitin sa totoo.</div>'}
    </section>
    <a class="btn secondary" href="#/summary">Ipakita sa midwife ang buod ko</a>
    <p class="muted small" style="text-align:center">Hindi tumatawag ang app nang kusa. Ikaw ang pipindot ng tawag.</p>`;
}

async function viewSummary(el) {
  const s = await api('/api/summary');
  const p = s.profile;
  el.innerHTML = `
    <div><h1 class="page-title">Buod para sa midwife</h1><p class="h-sub">${esc(fmtLong(s.today))}</p></div>
    <section class="card">${p ? `<div><strong>${esc(p.name || 'Pasyente')}</strong></div><div>Linggo ${p.weeks}${p.days ? ' + ' + p.days + ' araw' : ''} · Trimester ${p.trimester}</div>
      <div>Tinatayang due date: ${esc(fmtLong(p.due_date))} <span class="muted small">(tantiya)</span></div>` : '<div class="empty">Wala pang profile.</div>'}</section>
    <section class="card"><h2 style="font-size:15px">Mga kinumpirmang gagawin</h2>${s.tasks.map((t) => `<div class="row"><span class="grow" style="font-size:14px">${esc(t.title)}${t.verify ? ' <span class="chip warn">i-verify</span>' : ''}</span>${dateChip(t)}</div>`).join('') || '<div class="empty">Wala.</div>'}</section>
    <section class="card"><h2 style="font-size:15px">Mga tanong para sa check-up</h2>${s.questions.map((q) => `<div style="font-size:14px">• ${esc(q)}</div>`).join('') || '<div class="empty">Wala.</div>'}</section>
    <p class="muted small">${esc(s.note)}</p>
    <button class="btn no-print" onclick="window.print()">I-print o i-save bilang PDF</button>
    <a class="btn secondary no-print" href="#/home">Bumalik</a>`;
}

// ------------------------------------------------------------------ events
document.addEventListener('click', async (ev) => {
  const el = ev.target.closest('[data-action]');
  if (!el) return;
  const a = el.dataset.action, n = N();
  try {
    if (a === 'toggle') { await post('/api/tasks/toggle', { id: el.dataset.id }); await render(); }
    else if (a === 'month') {
      let { year, month } = state.month; month += Number(el.dataset.delta);
      if (month < 1) { month = 12; year -= 1; } if (month > 12) { month = 1; year += 1; }
      state.month = { year, month }; await render();
    }
    else if (a === 'rec-start') await startRecording();
    else if (a === 'rec-stop') stopRecording();
    else if (a === 'sample') {
      const s = await api('/api/sample'); n.transcript = s.transcript; n.visitDate = s.visit_date; n.consent = true; renderNotes();
      toast('Sample na usapan: kathang-isip, hindi totoong konsultasyon.');
    }
    else if (a === 'analyze') await analyze();
    else if (a === 'save-tasks') await saveTasks();
    else if (a === 'notes-back') { n.stage = 'input'; renderNotes(); }
    else if (a === 'notes-reset') { Object.assign(n, { stage: 'input', transcript: '', result: null, picked: new Set(), pickedQ: new Set() }); renderNotes(); }
    else if (a === 'kind') { state.nearby.kind = el.dataset.kind; await render(); }
    else if (a === 'locate') locate();
  } catch (err) { toast(err.message); }
});

document.addEventListener('change', (ev) => {
  const el = ev.target.closest('[data-action]');
  if (!el) return;
  const n = N(), a = el.dataset.action, i = Number(el.dataset.i);
  if (a === 'consent') { n.consent = el.checked; n.transcript = $('#transcript')?.value ?? n.transcript; renderNotes(); }
  else if (a === 'pick') { el.checked ? n.picked.add(i) : n.picked.delete(i); renderNotes(); }
  else if (a === 'pickq') { el.checked ? n.pickedQ.add(i) : n.pickedQ.delete(i); renderNotes(); }
});

document.addEventListener('input', (ev) => {
  if (ev.target.id === 'transcript') N().transcript = ev.target.value;
  if (ev.target.id === 'visitDate') N().visitDate = ev.target.value;
});

document.addEventListener('submit', async (ev) => {
  const f = ev.target.closest('[data-form]');
  if (!f) return;
  ev.preventDefault();
  const data = Object.fromEntries(new FormData(f));
  try {
    if (f.dataset.form === 'profile') { await post('/api/profile', data); await render(); }
    else if (f.dataset.form === 'ask') { if (data.q.trim()) await ask(data.q.trim()); }
  } catch (err) { toast(err.message); }
});

(async function start() {
  try { state.health = await api('/api/health'); } catch (_) { state.health = null; }
  renderBanner();
  await render();
})();
