// StreamHub phone app
(() => {
  // ---------- icons ----------
  const P = {
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    micoff: '<path d="M3 3l18 18"/><path d="M9 9v2a3 3 0 0 0 5.1 2.1M15 10V6a3 3 0 0 0-5.7-1.3"/><path d="M5 11a7 7 0 0 0 11.5 5.4M19 11a7 7 0 0 1-.5 2.5M12 18v3"/>',
    headphones: '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="14" width="5" height="7" rx="2"/><rect x="16" y="14" width="5" height="7" rx="2"/>',
    headoff: '<path d="M3 3l18 18"/><path d="M4 15v-3a8 8 0 0 1 2.3-5.7M9 4.6A8 8 0 0 1 20 12v3"/><rect x="3" y="14" width="5" height="7" rx="2"/><path d="M16 16v3a2 2 0 0 0 2 2h1"/>',
    speaker: '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M17 9a4 4 0 0 1 0 6"/>',
    speakeroff: '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M17 9l4 6M21 9l-4 6"/>',
    star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    play: '<path d="M7 4l13 8-13 8z" fill="currentColor"/>',
    pause: '<rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/><rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/>',
    next: '<path d="M5 5l10 7-10 7z" fill="currentColor"/><path d="M19 5v14"/>',
    prev: '<path d="M19 5L9 12l10 7z" fill="currentColor"/><path d="M5 5v14"/>',
    music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    users: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-4-6.3"/>',
    video: '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="M16 10l6-4v12l-6-4"/>',
  };
  const svg = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${P[n] || ''}</svg>`;
  const paintIcons = (root = document) => root.querySelectorAll('[data-icon]').forEach(el => {
    if (el.dataset.painted !== el.dataset.icon) { el.innerHTML = svg(el.dataset.icon); el.dataset.painted = el.dataset.icon; }
  });
  const setIcon = (el, n) => { if (el && el.dataset.icon !== n) { el.dataset.icon = n; paintIcons(el.parentNode); } };

  // ---------- helpers ----------
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const buzz = () => { try { navigator.vibrate && navigator.vibrate(12); } catch (e) {} };
  const S = { discord: {}, obs: {}, media: {}, automute: {}, meter: { db: -100 }, update: {} };

  // ---------- key / connection ----------
  // The key stays in the address so "Add to Home Screen" saves it too
  // (home-screen apps on iPhone don't share storage with Safari).
  const url = new URL(location.href);
  let key = url.searchParams.get('key') || '';
  try {
    if (key) localStorage.setItem('sh_key', key);
    else key = localStorage.getItem('sh_key') || '';
  } catch (e) {}
  if (key && url.searchParams.get('key') !== key) history.replaceState(null, '', `/?key=${encodeURIComponent(key)}`);
  function askForKey() {
    const k = prompt('Pairing code (the part after "key=" on your PC\'s pair page):');
    if (k && k.trim()) location.href = `/?key=${encodeURIComponent(k.trim().replace(/^.*key=/, ''))}`;
  }
  let ws, retry = 0, authed = false;

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws?kind=app&key=${encodeURIComponent(key)}`);
    ws.onopen = () => { retry = 0; };
    ws.onmessage = (e) => { const m = JSON.parse(e.data); onMsg(m.t, m.d); };
    ws.onclose = () => {
      authed = false;
      if (banner.dataset.auth === 'bad') return;
      showBanner('Reconnecting to your PC…');
      setTimeout(connect, Math.min(5000, 500 + retry++ * 700));
    };
  }
  const banner = $('banner');
  banner.addEventListener('click', () => { if (banner.dataset.auth === 'bad') askForKey(); });
  function showBanner(t) { banner.textContent = t; banner.classList.remove('hidden'); }
  function hideBanner() { banner.classList.add('hidden'); }

  function send(cmd, extra = {}) {
    if (!ws || ws.readyState !== 1 || !authed) return toast('Not connected to your PC', 'error');
    buzz();
    ws.send(JSON.stringify({ cmd, ...extra }));
  }

  function onMsg(t, d) {
    if (t === 'auth') {
      if (!d.ok) { banner.dataset.auth = 'bad'; showBanner('Not paired. Scan the QR code on your PC\'s pair page, or tap here to type the code.'); return; }
      authed = true; hideBanner(); return;
    }
    if (t === 'toast') return toast(d.msg, d.level);
    S[t] = d;
    if (t === 'discord') { renderDiscord(); renderMembers(); renderAutomute(); }
    else if (t === 'obs') renderOBS();
    else if (t === 'media') renderMedia();
    else if (t === 'automute') { renderAutomute(); renderMembers(); }
    else if (t === 'meter') renderMeter();
    else if (t === 'update') renderUpdate();
    renderDots();
  }

  // ---------- toasts ----------
  function toast(msg, level = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${level}`; el.textContent = msg;
    $('toasts').appendChild(el);
    if (level === 'warn') buzz();
    setTimeout(() => el.remove(), 3800);
  }

  // ---------- tabs ----------
  document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x.id === `tab-${b.dataset.tab}`));
    localStorage.setItem('sh_tab', b.dataset.tab);
    window.scrollTo(0, 0);
  }));
  const savedTab = localStorage.getItem('sh_tab');
  if (savedTab) document.querySelector(`.tabs button[data-tab="${savedTab}"]`)?.click();

  // generic data-cmd buttons (event delegation)
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-cmd]');
    if (!b) return;
    const cmd = b.dataset.cmd, extra = b.dataset.name ? { name: b.dataset.name } : {};
    const ask = confirmFor(cmd);
    if (ask) confirmSheet(ask).then(ok => ok && send(cmd, extra));
    else send(cmd, extra);
  });

  // ---------- "are you sure?" for stream / record ----------
  function confirmFor(cmd) {
    const o = S.obs || {};
    if (cmd === 'update.apply') return { title: `Update to ${S.update.latest}?`,
      text: 'StreamHub will download the new version and restart. Takes about 10 seconds. Your settings stay as they are.', ok: 'Update now', danger: false };
    if (cmd === 'obs.toggleStream') return o.streaming
      ? { title: 'End stream?', text: 'Your stream will go offline for everyone watching.', ok: 'End stream', danger: true }
      : { title: 'Go live?', text: `You'll start streaming on scene "${o.scene || '?'}".`, ok: 'Go live', danger: false };
    if (cmd === 'obs.toggleRecord') return o.recording
      ? { title: 'Stop recording?', text: 'The recording will be saved and closed.', ok: 'Stop recording', danger: true }
      : { title: 'Start recording?', text: 'OBS will start recording to your recordings folder.', ok: 'Start recording', danger: false };
    return null;
  }
  function confirmSheet({ title, text, ok, danger }) {
    return new Promise(resolve => {
      buzz();
      const wrap = document.createElement('div');
      wrap.className = 'sheet-wrap';
      wrap.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">
        <h2>${esc(title)}</h2><p class="muted">${esc(text)}</p>
        <button class="sheet-ok ${danger ? 'danger' : ''}">${esc(ok)}</button>
        <button class="sheet-cancel">Cancel</button></div>`;
      const done = (v) => { wrap.classList.remove('open'); setTimeout(() => wrap.remove(), 200); resolve(v); };
      wrap.addEventListener('click', (e) => { if (e.target === wrap) done(false); });
      wrap.querySelector('.sheet-ok').addEventListener('click', (e) => { e.stopPropagation(); done(true); });
      wrap.querySelector('.sheet-cancel').addEventListener('click', (e) => { e.stopPropagation(); done(false); });
      document.body.appendChild(wrap);
      requestAnimationFrame(() => wrap.classList.add('open'));
    });
  }

  // ---------- status dots ----------
  function renderDots() {
    const d = [['Discord', S.discord.connected], ['OBS', S.obs.connected], ['Music', S.media.available !== false]];
    $('dots').innerHTML = d.map(([n, ok]) => `<i class="${ok ? 'ok' : ''}">${n}</i>`).join('');
  }

  // ---------- discord ----------
  function renderDiscord() {
    const d = S.discord, mute = $('btnMute'), deaf = $('btnDeaf');
    mute.classList.toggle('on', !!d.mute || !!d.deaf);
    deaf.classList.toggle('on', !!d.deaf);
    mute.classList.toggle('off', !d.connected); deaf.classList.toggle('off', !d.connected);
    setIcon(mute.querySelector('.ic'), d.mute || d.deaf ? 'micoff' : 'mic');
    setIcon(deaf.querySelector('.ic'), d.deaf ? 'headoff' : 'headphones');
    mute.querySelector('b').textContent = d.mute || d.deaf ? 'Muted' : 'Mic on';
    deaf.querySelector('b').textContent = d.deaf ? 'Deafened' : 'Deafen';

    const vm = $('voiceMini');
    if (!d.connected) {
      vm.innerHTML = `<div class="muted small"><b>Discord:</b> ${esc(d.status || 'not connected')}</div>`;
    } else if (!d.channel) {
      vm.innerHTML = `<div class="muted">Not in a voice channel</div>`;
    } else {
      const sp = new Set(d.speaking || []);
      const avs = (d.members || []).slice(0, 8).map(m => `<img src="${esc(m.avatar)}" class="${sp.has(m.id) ? 'sp' : ''}" alt="">`).join('');
      vm.innerHTML = `<div class="vm"><div class="stack">${avs}</div><div><b>${esc(d.channel.name)}</b>
        <div class="muted small">${esc(d.channel.guild || 'Call')} · ${(d.members || []).length} in voice</div></div></div>`;
    }
    $('vcTitle').textContent = d.channel ? d.channel.name : 'Voice';
    $('vcGuild').textContent = d.channel ? (d.channel.guild || '') : '';
  }

  // members (keyed so sliders don't jump while dragging)
  const rows = new Map();
  function renderMembers() {
    const d = S.discord, box = $('members');
    const list = d.connected && d.channel ? (d.members || []) : [];
    if (!list.length) {
      rows.clear();
      box.innerHTML = `<div class="empty">${d.connected ? 'Join a voice channel in Discord and people show up here.' : esc(d.status || 'Discord not connected')}</div>`;
      return;
    }
    if (box.querySelector('.empty')) box.innerHTML = '';
    const sp = new Set(d.speaking || []);
    const wl = new Set((S.automute.whitelist) || []);
    const auto = new Set(((S.automute.muted) || []).map(m => m.id));
    const ids = new Set(list.map(m => m.id));
    for (const [id, el] of rows) if (!ids.has(id)) { el.remove(); rows.delete(id); }
    list.forEach((m, i) => {
      const isMe = d.me && m.id === d.me.id;
      let el = rows.get(m.id);
      if (!el) {
        el = document.createElement('div');
        el.className = 'member';
        el.innerHTML = `<div class="head"><img class="av" alt=""><div class="who"><b></b><div class="flags"></div></div>
          <button class="star" title="Never auto-mute"><span data-icon="star"></span></button>
          <button class="tog lm"><span data-icon="speaker"></span></button></div>
          <div class="vol"><input type="range" min="0" max="200" step="5"><span></span></div>`;
        const slider = el.querySelector('input'), lbl = el.querySelector('.vol span');
        slider.addEventListener('pointerdown', () => slider.dataset.drag = '1');
        slider.addEventListener('input', () => lbl.textContent = slider.value + '%');
        slider.addEventListener('change', () => { delete slider.dataset.drag; send('discord.user', { id: m.id, volume: +slider.value }); });
        el.querySelector('.lm').addEventListener('click', () => {
          const cur = S.discord.members.find(x => x.id === m.id);
          send('discord.user', { id: m.id, mute: !(cur && cur.local_mute) });
        });
        el.querySelector('.star').addEventListener('click', () => {
          send('automute.whitelist', { id: m.id, on: !(S.automute.whitelist || []).includes(m.id) });
        });
        rows.set(m.id, el);
        paintIcons(el);
      }
      if (box.children[i] !== el) box.insertBefore(el, box.children[i] || null);
      el.classList.toggle('speaking', sp.has(m.id));
      el.classList.toggle('lmuted', m.local_mute);
      const img = el.querySelector('.av'); if (img.getAttribute('src') !== m.avatar) img.src = m.avatar;
      el.querySelector('.who b').textContent = m.name + (isMe ? ' (you)' : '');
      const flags = [];
      if (auto.has(m.id)) flags.push('auto-muted');
      else if (m.local_mute) flags.push('muted by you');
      if (m.self_deaf || m.server_deaf) flags.push('deafened'); else if (m.self_mute || m.server_mute) flags.push('mic off');
      if (wl.has(m.id)) flags.push('protected');
      el.querySelector('.flags').textContent = flags.join(' · ') || (sp.has(m.id) ? 'talking' : '');
      const lm = el.querySelector('.lm');
      lm.classList.toggle('on', m.local_mute);
      setIcon(lm.querySelector('[data-icon]'), m.local_mute ? 'speakeroff' : 'speaker');
      lm.style.display = isMe ? 'none' : '';
      el.querySelector('.star').style.display = isMe ? 'none' : '';
      el.querySelector('.star').classList.toggle('on', wl.has(m.id));
      el.querySelector('.vol').style.display = isMe ? 'none' : '';
      const slider = el.querySelector('input');
      if (!slider.dataset.drag) { slider.value = m.volume; el.querySelector('.vol span').textContent = Math.round(m.volume) + '%'; }
    });
  }

  // ---------- automute ----------
  const amThr = $('amThr'), amHold = $('amHold'), amDur = $('amDur'), amEnabled = $('amEnabled');
  const durText = (v) => +v === 0 ? 'until I unmute' : `${v}s`;
  function bindSlider(el, label, fmtFn, keyName, parse = Number) {
    el.addEventListener('pointerdown', () => el.dataset.drag = '1');
    el.addEventListener('input', () => { label.textContent = fmtFn(el.value); if (el === amThr) renderMeter(); });
    el.addEventListener('change', () => { delete el.dataset.drag; send('automute.settings', { settings: { [keyName]: parse(el.value) } }); });
  }
  bindSlider(amThr, $('thrVal'), v => `${(+v).toFixed(1)} dB`, 'threshold_db');
  bindSlider(amHold, $('holdVal'), v => `${v} ms`, 'hold_ms');
  bindSlider(amDur, $('durVal'), durText, 'duration_s');
  amEnabled.addEventListener('change', () => send('automute.settings', { settings: { enabled: amEnabled.checked } }));

  function renderAutomute() {
    const a = S.automute; if (!a || a.enabled === undefined) return;
    amEnabled.checked = a.enabled;
    if (!amThr.dataset.drag) { amThr.value = a.threshold_db; $('thrVal').textContent = `${(+a.threshold_db).toFixed(1)} dB`; }
    if (!amHold.dataset.drag) { amHold.value = a.hold_ms; $('holdVal').textContent = `${a.hold_ms} ms`; }
    if (!amDur.dataset.drag) { amDur.value = a.duration_s; $('durVal').textContent = durText(a.duration_s); }
    $('amMuted').innerHTML = (a.muted || []).map(m => `<div class="amrow"><span>Auto-muted <b>${esc(m.name)}</b>
      ${m.remaining != null ? `<span class="muted small" data-rem="${m.remaining}">· ${m.remaining}s</span>` : ''}</span>
      <button data-unmute="${esc(m.id)}">Unmute</button></div>`).join('');
    $('amHistory').innerHTML = (a.history || []).length
      ? 'Recent: ' + a.history.slice(0, 4).map(h => `${esc(h.name)} (${new Date(h.at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })})`).join(', ')
      : '';
    renderMeter();
  }
  $('amMuted').addEventListener('click', (e) => {
    const b = e.target.closest('[data-unmute]'); if (b) send('automute.unmute', { id: b.dataset.unmute });
  });
  setInterval(() => document.querySelectorAll('[data-rem]').forEach(el => {
    const r = Math.max(0, +el.dataset.rem - 1); el.dataset.rem = r; el.textContent = `· ${r}s`;
  }), 1000);

  const pct = (db) => Math.max(0, Math.min(100, (db + 60) / 60 * 100));
  let meterTs = 0;
  function renderMeter() {
    const db = S.meter.db ?? -100;
    meterTs = S.meter === renderMeter.last ? meterTs : Date.now(); renderMeter.last = S.meter;
    $('meterFill').style.width = pct(db) + '%';
    $('meterThr').style.left = `calc(${pct(+amThr.value)}% - 1px)`;
    $('meterDb').textContent = db <= -99 ? 'silent' : `${db.toFixed(1)} dB`;
    let hint = '';
    if (!S.obs.connected) hint = 'Needs OBS open';
    else if (!S.obs.discord_source_found) hint = `No "${S.obs.discord_source}" audio source in OBS`;
    else if (Date.now() - meterTs > 2000) hint = 'No signal';
    $('meterHint').textContent = hint;
  }
  setInterval(renderMeter, 1000);

  // ---------- OBS ----------
  function renderOBS() {
    const o = S.obs;
    [['btnStream', 'btnStream2'], ['btnRecord', 'btnRecord2']].forEach(([a, b], i) => {
      const on = i === 0 ? o.streaming : o.recording;
      const label = i === 0 ? (on ? 'LIVE' : 'Go live') : (on ? 'Recording' : 'Record');
      [a, b].forEach(id => { $(id).classList.toggle('on', !!on); $(id).querySelector('b').textContent = label; });
    });
    $('obsStatus').textContent = o.connected ? '' : (o.status || '');
    const scenes = o.scenes || [];
    $('sceneChips').innerHTML = o.connected
      ? scenes.map(s => `<button class="chip ${s === o.scene ? 'on' : ''}" data-cmd="obs.scene" data-name="${esc(s)}">${esc(s)}</button>`).join('')
      : `<span class="muted small">${esc(o.status || 'OBS not connected')}</span>`;
    $('sceneGrid').innerHTML = scenes.map(s => `<button class="scene ${s === o.scene ? 'on' : ''}" data-cmd="obs.scene" data-name="${esc(s)}">${esc(s)}</button>`).join('')
      || `<div class="empty" style="grid-column:1/-1">${esc(o.status || 'OBS not connected')}</div>`;
    $('audioList').innerHTML = (o.inputs || []).map(i => `<div class="li"><span class="name">${esc(i.name)}</span>
      <button class="tog ${i.muted ? 'on' : ''}" data-cmd="obs.toggleInput" data-name="${esc(i.name)}">
      <span data-icon="${i.muted ? 'speakeroff' : 'speaker'}"></span>${i.muted ? 'Muted' : 'On'}</button></div>`).join('')
      || `<div class="empty">No audio sources</div>`;
    paintIcons($('audioList'));
    renderMeter();
  }

  // ---------- media ----------
  let mediaBase = 0;
  function renderMedia() {
    const m = S.media; mediaBase = Date.now();
    const active = m.available !== false && m.active;
    $('plTitle').textContent = active ? m.title : (m.available === false ? (m.reason || 'Unavailable') : 'Nothing playing');
    $('plArtist').textContent = active ? m.artist : (m.available === false ? '' :
      (m.reason ? m.reason + '. ' : '') + 'Playing on Spotify? In Spotify → Settings → Display, turn on "Show desktop overlay when using media keys", then skip a song.');
    $('plApp').textContent = active ? (m.app || '') : '';
    const img = $('plArt');
    if (active && m.art) { if (img.getAttribute('src') !== m.art) img.src = m.art; img.classList.remove('hidden'); $('plNoArt').classList.add('hidden'); }
    else { img.classList.add('hidden'); $('plNoArt').classList.remove('hidden'); }
    setIcon($('plPlay').querySelector('[data-icon]'), m.playing ? 'pause' : 'play');
    $('npMini').innerHTML = active
      ? `${m.art ? `<img src="${esc(m.art)}" alt="">` : `<div class="noart" data-icon="music"></div>`}
         <div class="t"><b>${esc(m.title)}</b><div class="muted small">${esc(m.artist)}</div></div>
         <button class="round" data-cmd="media.playpause"><span data-icon="${m.playing ? 'pause' : 'play'}"></span></button>
         <button class="round" data-cmd="media.next"><span data-icon="next"></span></button>`
      : `<div class="noart" data-icon="music"></div><div class="t muted">Nothing playing</div>`;
    paintIcons($('npMini'));
    tickProgress();
  }
  function tickProgress() {
    const m = S.media; if (!m || !m.active) { $('plBar').style.width = '0'; return; }
    const pos = Math.min(m.duration || 0, (m.position || 0) + (m.playing ? (Date.now() - mediaBase) / 1000 : 0));
    $('plBar').style.width = m.duration ? (pos / m.duration * 100) + '%' : '0';
    $('plPos').textContent = fmt(pos); $('plDur').textContent = fmt(m.duration);
  }
  setInterval(tickProgress, 1000);

  // ---------- updates ----------
  let pageVersion = null;
  function renderUpdate() {
    const u = S.update || {};
    // after an update restarts the server, reload so the phone gets the new app too
    if (u.current) {
      if (pageVersion && pageVersion !== u.current) { location.reload(); return; }
      pageVersion = u.current;
    }
    const card = $('updCard');
    if (u.available || u.busy) {
      card.classList.remove('hidden');
      card.innerHTML = u.busy
        ? `<div><b>Updating…</b><div class="muted small">Hang tight, StreamHub will restart.</div></div>`
        : `<div><b>Update available</b><div class="muted small">Version ${esc(u.latest)} (you have ${esc(u.current)})</div></div>
           <button class="upd-btn" data-cmd="update.apply">Update</button>`;
    } else card.classList.add('hidden');
    $('verLine').innerHTML = u.current
      ? `StreamHub ${esc(u.current)}${u.configured ? ` · <a href="#" id="chk">Check for updates</a>` : ''}${u.error ? `<br>${esc(u.error)}` : ''}`
      : '';
  }
  document.addEventListener('click', (e) => {
    if (e.target.id === 'chk') { e.preventDefault(); send('update.check'); toast('Checking…'); }
  });

  // ---------- go ----------
  paintIcons();
  renderDots(); renderDiscord(); renderMembers(); renderOBS(); renderMedia();
  connect();
})();
