/* PTP web app. Plain JavaScript, no build step, no external files: works with the network off.
   Every piece of screen text is in strings.json, in English and Tagalog. t(name) returns it in the
   language the user picked (English until they switch). AI answers are not translated: they follow
   the language the question or visit was in. */
'use strict';

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const KIND_COLORS = { rhu: '#2a5db0', midwife: '#8a4fb3', hospital: '#b3261e' };
const LANGS = { en: 'English', tl: 'Tagalog' }; // each name in its own language, so it is never translated
const LANG_KEY = 'ptp.lang';
const isLang = (l) => l === 'en' || l === 'tl';

let STRINGS = null; // {en: {...}, tl: {...}}, loaded from /strings.json at start

const state = {
  lang: 'en',
  health: null,
  month: null,                       // {year, month}
  chat: [],
  nearby: { kind: 'all', loc: null }, // loc = {lat, lon} from the device, or null for the demo location
  notes: { stage: 'input', consent: false, transcript: '', visitDate: '', result: null, picked: new Set(), pickedQ: new Set(),
           recording: false, seconds: 0, message: '' },
};

// ------------------------------------------------------------------ language
const S = () => STRINGS[state.lang];

/* Text for key in the current language, with {name} placeholders filled from vars.
   When vars.n is 1 and a "key_one" exists, that is used instead ("+1 day", not "+1 days"). */
function t(key, vars = {}) {
  const pick = (d) => (vars.n === 1 && d[key + '_one'] !== undefined ? d[key + '_one'] : d[key]);
  const text = pick(S()) ?? pick(STRINGS.en) ?? key;
  return text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

function savedLang() {
  try {
    const l = localStorage.getItem(LANG_KEY);
    return isLang(l) ? l : 'en';
  } catch (_) { return 'en'; } // storage blocked: start in English
}

function applyLang(lang) {
  state.lang = isLang(lang) ? lang : 'en';
  document.documentElement.lang = state.lang === 'tl' ? 'fil' : 'en';
  document.querySelectorAll('[data-t]').forEach((el) => { el.textContent = t(el.dataset.t); });
  $('#tabs').setAttribute('aria-label', t('tab.menu'));
}

async function switchLang(lang) {
  try { localStorage.setItem(LANG_KEY, lang); } catch (_) { /* storage blocked: keep it for this visit only */ }
  // Keep anything typed in the setup form; switching language re-draws it.
  const form = $('form[data-form=profile]');
  const typed = form ? Object.fromEntries(new FormData(form)) : null;
  applyLang(lang);
  renderBanner();
  await render();
  if (typed) {
    for (const [name, value] of Object.entries(typed)) {
      const input = $(`form[data-form=profile] [name="${name}"]`);
      if (input) input.value = value;
    }
  }
}

function langToggle(big = false) {
  const btn = (l) => `<button type="button" class="${state.lang === l ? 'on' : ''}" data-action="lang" data-lang="${l}"
    aria-pressed="${state.lang === l}" aria-label="${LANGS[l]}" lang="${l === 'tl' ? 'fil' : 'en'}">${big ? LANGS[l] : l.toUpperCase()}</button>`;
  return `<div class="lang${big ? ' big' : ''}" role="group" aria-label="Language / Wika">${btn('en')}${btn('tl')}</div>`;
}

// ------------------------------------------------------------------ helpers
function errorText(code) {
  const key = 'error.' + code;
  return code && S()[key] !== undefined ? t(key, { model: state.health?.llm_model || '' }) : t('error.generic');
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const err = new Error(errorText(data.code));
    err.code = data.code; err.status = res.status;
    throw err;
  }
  return data;
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function parseISO(iso) { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); }
function fmtDay(iso) { const d = parseISO(iso); return `${S().weekdays[d.getDay()]}, ${S().monthsShort[d.getMonth()]} ${d.getDate()}`; }
function fmtShort(iso) { const d = parseISO(iso); return `${S().monthsShort[d.getMonth()]} ${d.getDate()}`; }
function fmtLong(iso) { const d = parseISO(iso); return `${S().monthsShort[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`; }
function fmtTime(hm) {
  if (!hm) return '';
  const [h, m] = hm.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? t('time.am') : t('time.pm')}`; // NU = nang umaga, NH = nang hapon
}
function dateChip(task) {
  if (!task.date) return '';
  const cls = task.kind === 'appointment' ? 'appt' : 'date';
  return `<span class="chip ${cls}">${esc(fmtShort(task.date))}${task.time ? ' · ' + esc(fmtTime(task.time)) : ''}</span>`;
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
  if (h.mode === 'mock') lines.push(esc(t('banner.mock')));
  else if (!h.llm_ready) lines.push(`<span class="bad">${esc(t('banner.noModel'))}</span> ${esc(t('banner.stillWorks'))}`);
  el.innerHTML = lines.join('<br>');
}

// ------------------------------------------------------------------ router
const routes = { home: viewHome, calendar: viewCalendar, notes: viewNotes, ask: viewAsk, nearby: viewNearby, emergency: viewEmergency, summary: viewSummary };

async function render() {
  const name = (location.hash.replace(/^#\/?/, '') || 'home').split('?')[0];
  const view = routes[name] ? name : 'home';
  const tabs = $('#tabs');
  tabs.classList.toggle('hidden', view === 'emergency');
  const tab = view === 'notes' ? 'home' : view; // Visit Notes is opened from the Record button on Home
  tabs.querySelectorAll('a').forEach((a) => a.classList.toggle('active', a.dataset.tab === tab));
  const el = $('#view');
  el.classList.toggle('no-tabs', view === 'emergency');
  try {
    await routes[view](el);
  } catch (err) {
    el.innerHTML = `<div class="note-red">${esc(err.message)}</div>`;
  }
}
window.addEventListener('hashchange', () => { $('#toast').classList.remove('show'); render(); $('#view').scrollTop = 0; });

// ------------------------------------------------------------------ home
function setupForm() {
  return `
    <h1 class="page-title">${esc(t('setup.title'))}</h1>
    <section class="card"><div class="muted small" style="font-weight:700">Language · Wika</div>${langToggle(true)}</section>
    <p class="h-sub">${esc(t('setup.intro'))}</p>
    <form class="card" data-form="profile">
      <label class="field">${esc(t('setup.name'))}<input type="text" name="name" autocomplete="off"></label>
      <label class="field">${esc(t('setup.lmp'))}<input type="date" name="lmp" required max="${esc(state.health?.today || '')}"></label>
      <button class="btn" type="submit">${esc(t('setup.save'))}</button>
    </form>`;
}

async function viewHome(el) {
  const h = await api('/api/home');
  if (!h.profile) { el.innerHTML = setupForm(); return; }
  const p = h.profile;
  const next = h.next_appointment;
  const tasks = h.tasks.map((task) => `
    <div class="row">
      <button class="box ${task.done ? 'on' : ''}" data-action="toggle" data-id="${esc(task.id)}" aria-label="${esc(t('home.markDone', { title: task.title }))}">${task.done ? icon.check : ''}</button>
      <span class="grow task-title ${task.done ? 'done' : ''}">${esc(task.title)}</span>${dateChip(task)}
    </div>`).join('');
  el.innerHTML = `
    <div class="row between" style="align-items:flex-start">
      <div><div class="muted" style="font-size:14px">${esc(t('home.hello'))}</div><h1 class="page-title">${esc(p.name || t('home.defaultName'))}</h1></div>
      <div class="head-side">${langToggle()}<span class="chip"><span class="dot"></span>&nbsp;${esc(t('home.onDevice'))}</span></div>
    </div>
    <section class="hero" aria-label="${esc(t('home.heroLabel'))}">
      <div class="row between" style="align-items:flex-end">
        <div class="row" style="align-items:baseline;gap:8px"><span class="big">${p.weeks}</span><span>${esc(t('home.ofWeeks'))}${p.days ? ' · ' + esc(t('home.plusDays', { n: p.days })) : ''}</span></div>
        <span class="chip">${esc(t('common.trimester', { t: p.trimester }))}</span>
      </div>
      <div class="bar"><i style="width:${Math.round(p.progress * 100)}%"></i></div>
      <div class="row between small" style="font-size:13px"><span>${esc(t('home.dueDate'))}</span><strong>${esc(fmtLong(p.due_date))}</strong></div>
      <div style="font-size:11px;opacity:.9">${esc(t('home.estimate'))}</div>
    </section>
    <a class="card row" href="#/calendar" style="flex-direction:row;text-decoration:none">
      <div class="iconbox">${icon.cal}</div>
      <div class="grow">
        <div class="muted small" style="font-weight:600">${esc(t('home.nextCheckup'))}</div>
        ${next ? `<div style="font-size:16px;font-weight:700">${esc(fmtDay(next.date))}${next.time ? ' · ' + esc(fmtTime(next.time)) : ''}</div><div class="muted" style="font-size:13px">${esc(next.title)}</div>`
               : `<div class="empty">${esc(t('home.noCheckup'))}</div>`}
      </div>
    </a>
    <section class="card">
      <div class="row between"><h2 style="font-size:16px">${esc(t('home.todo'))}</h2><a href="#/summary" style="font-size:13px;font-weight:700;color:var(--primary);text-decoration:none">${esc(t('home.summaryLink'))}</a></div>
      ${tasks || `<div class="empty">${esc(t('home.noTasks'))}</div>`}
    </section>
    <a class="btn" href="#/notes">${icon.mic} ${esc(t('home.record'))}</a>`;
}

// ------------------------------------------------------------------ calendar
async function viewCalendar(el) {
  if (!state.month) { const d = new Date((state.health?.today || new Date().toISOString().slice(0, 10)) + 'T00:00:00'); state.month = { year: d.getFullYear(), month: d.getMonth() + 1 }; }
  const c = await api(`/api/calendar?year=${state.month.year}&month=${state.month.month}`);
  const cells = Array.from({ length: c.lead_blanks }, () => '<div class="cell"></div>').join('') + c.days.map((d) => {
    const kinds = new Set(d.items.map((i) => i.kind));
    const pip = kinds.has('appointment') ? 'appointment' : (d.items.length ? 'task' : '');
    const cls = d.today ? 'today' : (d.due ? 'due' : '');
    return `<div class="cell ${cls}"><div class="num">${d.n}</div><div class="pip ${pip}"></div></div>`;
  }).join('');
  const p = c.profile;
  const agenda = c.agenda.map((task) => `<div class="row"><span class="dot" style="background:${task.kind === 'appointment' ? '#c4472a' : 'var(--primary)'}"></span><span class="grow" style="font-size:14px">${esc(task.title)}</span><span class="muted" style="font-size:13px">${esc(fmtShort(task.date))}${task.time ? ' · ' + esc(fmtTime(task.time)) : ''}</span></div>`).join('');
  el.innerHTML = `
    <div class="row between"><h1 class="page-title">${esc(t('cal.title'))}</h1>${p ? `<span class="chip appt">${esc(t('cal.due', { date: fmtLong(p.due_date) }))}</span>` : ''}</div>
    <section class="card" style="padding:14px 12px 8px">
      <div class="row between" style="padding:0 4px 6px">
        <button class="navbtn" data-action="month" data-delta="-1" aria-label="${esc(t('cal.prev'))}">${icon.left}</button>
        <strong style="font-size:20px">${esc(S().months[c.month - 1])} ${c.year}</strong>
        <button class="navbtn" data-action="month" data-delta="1" aria-label="${esc(t('cal.next'))}">${icon.right}</button>
      </div>
      <div class="cal">${S().weekdaysShort.map((w) => `<div class="wd">${esc(w)}</div>`).join('')}${cells}</div>
    </section>
    ${p ? `<section class="card">
      <h2 style="font-size:15px">${esc(t('cal.timeline'))}</h2>
      <div class="tl" role="img" aria-label="${esc(t('cal.timelineLabel', { w: p.weeks }))}">
        <i style="left:0;width:32.5%;background:var(--t1);border-radius:5px 0 0 5px"></i>
        <i style="left:32.5%;width:35%;background:var(--t2)"></i>
        <i style="left:67.5%;width:32.5%;background:var(--t3);border-radius:0 5px 5px 0"></i>
        <i class="mark" style="left:${Math.round(p.progress * 1000) / 10}%"></i>
      </div>
      <div class="row between muted small"><span>${esc(t('common.trimester', { t: 1 }))}</span><span>${esc(t('common.trimester', { t: 2 }))}</span><span>${esc(t('common.trimester', { t: 3 }))}</span></div>
      <div class="muted small">${esc(t('cal.youAreHere', { w: p.weeks }))}</div>
    </section>` : ''}
    <section class="card"><h2 style="font-size:15px">${esc(t('cal.upcoming'))}</h2>${agenda || `<div class="empty">${esc(t('cal.nothing'))}</div>`}</section>`;
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
    body = `<div class="card recbox"><div class="spinner"></div><div>${esc(n.message || t('notes.wait'))}</div><div class="muted small">${esc(t('notes.local'))}</div></div>`;
  } else if (n.stage === 'saved') {
    body = `<div class="card recbox"><div class="iconbox" style="width:56px;height:56px">${icon.cal}</div><strong style="font-size:18px">${esc(t('notes.saved'))}</strong>
      <a class="btn" href="#/calendar">${esc(t('notes.viewCalendar'))}</a><button class="btn secondary" data-action="notes-reset">${esc(t('notes.new'))}</button></div>`;
  } else if (n.stage === 'review' && n.result) {
    body = reviewHTML(n);
  } else {
    const canRecord = !!(navigator.mediaDevices && window.MediaRecorder) && !!h.stt;
    const why = !h.stt ? t('notes.noStt') : (!(navigator.mediaDevices && window.MediaRecorder) ? t('notes.noRecorder') : '');
    body = `
      <label class="card check"><input type="checkbox" data-action="consent" ${n.consent ? 'checked' : ''}><span style="font-size:14px;line-height:1.4">${esc(t('notes.consent'))}</span></label>
      <section class="card recbox" aria-label="${esc(t('notes.recordLabel'))}">
        ${n.recording
          ? `<span class="chip red"><span class="dot" style="background:var(--red)"></span>&nbsp;${esc(t('notes.recording'))}</span>
             <div class="timer" id="timer">${fmtSecs(n.seconds)}</div>
             <div class="wave" aria-hidden="true">${Array.from({ length: 24 }, (_, i) => `<i style="height:${18 + ((i * 37) % 42)}px;animation-delay:${(i % 8) * 0.11}s"></i>`).join('')}</div>
             <button class="recbtn" data-action="rec-stop" aria-label="${esc(t('notes.stop'))}"><i></i></button>`
          : `<button class="recbtn start" data-action="rec-start" aria-label="${esc(t('notes.start'))}" ${canRecord && n.consent ? '' : 'disabled style="opacity:.5;cursor:not-allowed"'}>${icon.mic}</button>
             <div class="muted small">${esc(n.consent ? (why || t('notes.tapToRecord')) : t('notes.consentFirst'))}</div>`}
        <div class="muted small">${esc(t('notes.voiceLocal'))}</div>
      </section>
      <label class="field">${esc(t('notes.transcript'))}
        <textarea id="transcript" placeholder="Midwife: …&#10;Maria: …">${esc(n.transcript)}</textarea></label>
      <label class="field">${esc(t('notes.visitDate'))}<input type="date" id="visitDate" value="${esc(n.visitDate)}" max="${esc(h.today || '')}"></label>
      <div class="row"><button class="btn secondary small" data-action="sample">${esc(t('notes.useSample'))}</button></div>
      <button class="btn" data-action="analyze">${esc(t('notes.analyze'))}</button>`;
  }
  el.innerHTML = `<div><h1 class="page-title">${esc(t('notes.title'))}</h1><p class="h-sub">${esc(t('notes.intro'))}</p></div>${body}`;
}

function reviewHTML(n) {
  const r = n.result;
  const tasks = r.tasks.map((task, i) => `
    <label class="card task check" style="flex-direction:row;gap:12px">
      <input type="checkbox" data-action="pick" data-i="${i}" ${n.picked.has(i) ? 'checked' : ''}>
      <div class="grow" style="display:flex;flex-direction:column;gap:5px">
        <div class="row between"><strong style="font-size:15px">${esc(task.title)}</strong>${dateChip(task)}</div>
        <div class="quote">“${esc(task.quote)}”</div>
        ${task.verify ? `<span class="chip warn" style="align-self:flex-start">${esc(t('review.verify'))}</span>` : ''}
        ${task.by_code ? `<span class="chip" style="align-self:flex-start">${esc(t('review.byCode'))}</span>` : ''}
      </div>
    </label>`).join('');
  const qs = r.unanswered.map((q, i) => `
    <label class="check" style="font-size:14px"><input type="checkbox" data-action="pickq" data-i="${i}" ${n.pickedQ.has(i) ? 'checked' : ''}><span>${esc(q.question)}</span></label>`).join('');
  const danger = esc(t('review.danger', { signs: r.emergency.join(', ') })).replace('{link}', `<a href="#/emergency">${esc(t('common.emergency'))}</a>`);
  return `
    ${r.emergency.length ? `<div class="note-red">${danger}</div>` : ''}
    ${r.model === 'mock' ? `<div class="note-warn">${esc(t('review.mock'))}</div>` : ''}
    ${r.summary ? `<section class="card"><strong class="small" style="color:var(--primary)">${esc(t('review.summary'))}</strong><div style="font-size:14px;line-height:1.45">${esc(r.summary)}</div></section>` : ''}
    <div class="muted small" style="font-weight:700">${esc(t('review.confirm'))}</div>
    ${tasks || `<div class="empty">${esc(t('review.none'))}</div>`}
    ${r.rejected ? `<div class="muted small">${esc(t('review.rejected', { n: r.rejected }))}</div>` : ''}
    ${qs ? `<section class="card"><strong class="small">${esc(t('review.questions'))}</strong>${qs}</section>` : ''}
    <button class="btn" data-action="save-tasks" ${n.picked.size || n.pickedQ.size ? '' : 'disabled'}>${esc(t('review.add'))}</button>
    <button class="btn secondary" data-action="notes-back">${esc(t('review.back'))}</button>
    <p class="muted small" style="text-align:center">${esc(t('review.disclaimer'))}</p>`;
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
    recTimer = setInterval(() => { N().seconds += 1; const timer = $('#timer'); if (timer) timer.textContent = fmtSecs(N().seconds); }, 1000);
    renderNotes();
  } catch (err) {
    toast(t('notes.micError', { error: err.message }));
  }
}

function stopRecording() {
  if (!recorder) return;
  recorder.stop();
  recorder.stream.getTracks().forEach((track) => track.stop());
  clearInterval(recTimer);
  N().recording = false;
}

async function onRecordingStopped() {
  const type = recorder.mimeType || 'audio/webm';
  const blob = new Blob(recChunks, { type });
  const ext = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'mp4' : 'webm';
  N().stage = 'working'; N().message = t('notes.transcribing'); renderNotes();
  try {
    const r = await api('/api/transcribe', { method: 'POST', headers: { 'X-Audio-Ext': ext }, body: blob });
    N().transcript = r.transcript;
    toast(t('notes.transcribed'));
  } catch (err) {
    toast(err.message);
  }
  N().stage = 'input'; renderNotes();
}

async function analyze() {
  const n = N();
  n.transcript = ($('#transcript')?.value ?? n.transcript).trim();
  n.visitDate = $('#visitDate')?.value || n.visitDate;
  if (!n.transcript) { toast(t('error.no_transcript')); return; }
  n.stage = 'working'; n.message = t('notes.analyzing'); renderNotes();
  try {
    n.result = await post('/api/extract', { transcript: n.transcript, visit_date: n.visitDate });
    n.picked = new Set(n.result.tasks.map((_, i) => i));
    n.pickedQ = new Set();
    n.stage = 'review';
  } catch (err) {
    toast(err.message);
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
  const log = state.chat.map((m, i) => {
    if (m.role === 'me') return `<div class="bubble me">${esc(m.text)}</div>`;
    const cls = m.kind === 'emergency' ? 'red' : (m.kind === 'answer' ? '' : 'amber');
    const cites = (m.citations || []).map((c) => `<span class="src">[${c.n}] ${esc(c.title)}${c.sample ? ' · ' + esc(t('ask.sampleCitation')) : ''}</span>`).join('');
    // The answer itself is in the language of the question; the notes around it follow the app language.
    return `<div class="bubble bot ${cls}"><div>${esc(m.kind === 'wait' ? t('ask.searching') : m.text)}</div>${cites}
      ${m.kind === 'emergency' ? `<a class="btn red small" href="#/emergency">${esc(t('ask.openEmergency'))}</a>` : ''}
      ${m.fromGuide ? `<div class="small muted">${esc(t('ask.fromGuide'))}</div>` : ''}
      ${m.offerSave && !m.saved ? `<button class="btn small" data-action="save-question" data-i="${i}">${esc(t('ask.saveQuestion'))}</button>` : ''}
      ${m.saved ? `<div class="small muted">${esc(t('ask.added'))}</div>` : ''}
      ${m.kind === 'answer' ? `<div class="small muted">${esc(t('common.notAdvice'))}</div>` : ''}</div>`;
  }).join('');
  el.innerHTML = `
    <div><h1 class="page-title">${esc(t('ask.title'))}</h1>
      <div class="chip" style="margin-top:8px">${esc(t('ask.source'))}</div>
      ${h.guides_are_sample ? `<div class="note-warn" style="margin-top:10px">${esc(t('ask.sampleGuides'))}</div>` : ''}</div>
    <div class="log" id="log">${log || `<div class="empty">${esc(t('ask.empty'))}</div>`}</div>
    <form class="composer" data-form="ask"><input type="text" name="q" placeholder="${esc(t('ask.placeholder'))}" autocomplete="off" aria-label="${esc(t('ask.label'))}" required>
      <button class="send" type="submit" aria-label="${esc(t('ask.send'))}">${icon.send}</button></form>`;
  const box = $('#view'); box.scrollTop = box.scrollHeight;
}

async function ask(q) {
  state.chat.push({ role: 'me', text: q });
  state.chat.push({ role: 'bot', kind: 'wait' });
  await viewAsk($('#view'));
  try {
    const r = await post('/api/ask', { question: q });
    state.chat[state.chat.length - 1] = { role: 'bot', kind: r.kind, text: r.answer, citations: r.citations, fromGuide: r.from_guide,
      offerSave: r.add_to_questions, question: q, saved: false };
  } catch (err) {
    state.chat[state.chat.length - 1] = { role: 'bot', kind: 'error', text: err.message };
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
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t('nearby.mapLabel'))}"><rect width="${W}" height="${H}" fill="#e6efea"/>
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
  const btn = (k, label) => `<button class="${state.nearby.kind === k ? 'on' : ''}" data-action="kind" data-kind="${k}">${esc(label)}</button>`;
  const where = !d.location.is_default ? t('nearby.yourLocation')
    : (d.sample ? t('nearby.exampleLocation', { name: d.location.name }) : d.location.name);
  el.innerHTML = `
    <div><h1 class="page-title">${esc(t('nearby.title'))}</h1><p class="h-sub">${esc(where)}</p></div>
    <div class="filters">${btn('all', t('nearby.all'))}${btn('hospital', t('nearby.hospital'))}${btn('midwife', t('nearby.midwife'))}${btn('rhu', t('nearby.rhu'))}</div>
    <div class="map">${mapSVG(d.location, shown)}<span class="tag">${esc(t('nearby.mapTag'))}</span></div>
    <button class="btn secondary small" data-action="locate">${esc(t('nearby.locate'))}</button>
    ${shown.map((f) => `<div class="facility"><span class="sw" style="background:${KIND_COLORS[f.kind] || '#555'}"></span>
       <div class="grow"><div style="font-size:15px;font-weight:700">${esc(f.name)}</div><div class="muted small">${esc(S().kinds[f.kind] || f.label || '')}</div></div>
       <strong>${f.distance_km.toFixed(1)} km</strong></div>`).join('') || `<div class="empty">${esc(t('nearby.none'))}</div>`}
    <p class="muted small">${esc(t('nearby.straightLine'))} ${d.sample ? `<strong>${esc(t('nearby.fictional'))}</strong>` : ''}</p>`;
}

function locate() {
  if (!navigator.geolocation) { toast(t('nearby.noGeo')); return; }
  navigator.geolocation.getCurrentPosition(
    (pos) => { state.nearby.loc = { lat: pos.coords.latitude, lon: pos.coords.longitude }; render(); },
    () => toast(t('nearby.geoFailed')),
    { timeout: 8000 });
}

// ------------------------------------------------------------------ emergency + summary
async function viewEmergency(el) {
  const [flags, near] = await Promise.all([api('/api/redflags'), api('/api/facilities' + (state.nearby.loc ? `?lat=${state.nearby.loc.lat}&lon=${state.nearby.loc.lon}` : ''))]);
  const first = near.facilities[0];
  // Each sign in the app language first, the other language after it (helpful for whoever is with her).
  const sign = (s) => (state.lang === 'tl' ? [s.tl, s.en] : [s.en, s.tl]);
  el.innerHTML = `
    <div class="em-head">
      <a class="back" href="#/home">${icon.left} ${esc(t('common.back'))}</a>
      <h1>${esc(t('common.emergency'))}</h1>
      <p style="font-size:17px;line-height:1.4">${esc(t('em.intro'))}</p>
      <a class="em-call" href="tel:911">${icon.phone} ${esc(t('em.call'))}</a>
    </div>
    ${first ? `<div class="card row" style="flex-direction:row"><div class="iconbox">${icon.cal.replace(/<rect.*<\/svg>/, '<path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>')}</div>
      <div class="grow"><div class="muted small" style="font-weight:600">${esc(t('em.nearest'))}</div><div style="font-size:16px;font-weight:700">${esc(first.name)}</div><div class="muted" style="font-size:13px">${esc(t('em.km', { km: first.distance_km.toFixed(1) }))}</div></div></div>` : ''}
    <section class="card">
      <h2 style="font-size:15px">${esc(t('em.signsTitle'))}</h2>
      ${flags.signs.map((s) => { const [main, other] = sign(s); return `<div class="sign"><i></i><span>${esc(main)} <span class="muted">· ${esc(other)}</span></span></div>`; }).join('')}
      ${flags.reviewed ? `<div class="muted small">${esc(t('em.source', { source: flags.source }))}</div>` : `<div class="note-warn">${esc(t('em.unreviewed'))}</div>`}
    </section>
    <a class="btn secondary" href="#/summary">${esc(t('em.showSummary'))}</a>
    <p class="muted small" style="text-align:center">${esc(t('em.noAutoCall'))}</p>`;
}

async function viewSummary(el) {
  const s = await api('/api/summary');
  const p = s.profile;
  el.innerHTML = `
    <div><h1 class="page-title">${esc(t('sum.title'))}</h1><p class="h-sub">${esc(fmtLong(s.today))}</p></div>
    <section class="card">${p ? `<div><strong>${esc(p.name || t('sum.patient'))}</strong></div><div>${esc(p.days ? t('sum.weekDays', { w: p.weeks, n: p.days }) : t('sum.week', { w: p.weeks }))} · ${esc(t('common.trimester', { t: p.trimester }))}</div>
      <div>${esc(t('sum.due', { date: fmtLong(p.due_date) }))} <span class="muted small">${esc(t('sum.estimate'))}</span></div>` : `<div class="empty">${esc(t('sum.noProfile'))}</div>`}</section>
    <section class="card"><h2 style="font-size:15px">${esc(t('sum.tasks'))}</h2>${s.tasks.map((task) => `<div class="row"><span class="grow" style="font-size:14px">${esc(task.title)}${task.verify ? ` <span class="chip warn">${esc(t('sum.verify'))}</span>` : ''}</span>${dateChip(task)}</div>`).join('') || `<div class="empty">${esc(t('sum.none'))}</div>`}</section>
    <section class="card"><h2 style="font-size:15px">${esc(t('sum.questions'))}</h2>${s.questions.map((q) => `<div style="font-size:14px">• ${esc(q)}</div>`).join('') || `<div class="empty">${esc(t('sum.none'))}</div>`}</section>
    <p class="muted small">${esc(t('sum.note'))}</p>
    <button class="btn no-print" onclick="window.print()">${esc(t('sum.print'))}</button>
    <a class="btn secondary no-print" href="#/home">${esc(t('common.back'))}</a>`;
}

// ------------------------------------------------------------------ events
document.addEventListener('click', async (ev) => {
  const el = ev.target.closest('[data-action]');
  if (!el) return;
  const a = el.dataset.action, n = N();
  try {
    if (a === 'toggle') { await post('/api/tasks/toggle', { id: el.dataset.id }); await render(); }
    else if (a === 'lang') await switchLang(el.dataset.lang);
    else if (a === 'month') {
      let { year, month } = state.month; month += Number(el.dataset.delta);
      if (month < 1) { month = 12; year -= 1; } if (month > 12) { month = 1; year += 1; }
      state.month = { year, month }; await render();
    }
    else if (a === 'rec-start') await startRecording();
    else if (a === 'rec-stop') stopRecording();
    else if (a === 'sample') {
      const s = await api('/api/sample'); n.transcript = s.transcript; n.visitDate = s.visit_date; n.consent = true; renderNotes();
      toast(t('notes.sampleToast'));
    }
    else if (a === 'analyze') await analyze();
    else if (a === 'save-tasks') await saveTasks();
    else if (a === 'notes-back') { n.stage = 'input'; renderNotes(); }
    else if (a === 'notes-reset') { Object.assign(n, { stage: 'input', transcript: '', result: null, picked: new Set(), pickedQ: new Set() }); renderNotes(); }
    else if (a === 'kind') { state.nearby.kind = el.dataset.kind; await render(); }
    else if (a === 'save-question') {
      const m = state.chat[Number(el.dataset.i)];
      await post('/api/questions', { question: m.question }); m.saved = true; await viewAsk($('#view'));
    }
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
  try {
    const res = await fetch('/strings.json');
    if (!res.ok) throw new Error(res.statusText);
    STRINGS = await res.json();
  } catch (_) {
    $('#view').innerHTML = '<div class="note-red">Could not load the app text. Restart the app.<br>Hindi ma-load ang teksto ng app. I-restart ang app.</div>';
    return;
  }
  applyLang(savedLang());
  try { state.health = await api('/api/health'); } catch (_) { state.health = null; }
  renderBanner();
  await render();
})();
