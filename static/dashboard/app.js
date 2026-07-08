(function () {
  'use strict';
  var DASHBOARD_BASE = window.location.pathname.replace(/\/?$/, '');
  var API_BASE = DASHBOARD_BASE.replace(/\/dashboard.*$/, '') || '/proxy';

  /* ---------- Auth helpers ---------- */
  function getKey() {
    var params = new URLSearchParams(window.location.search);
    var key = params.get('key') || localStorage.getItem('proxy_admin_key') || '';
    if (key) localStorage.setItem('proxy_admin_key', key);
    return key;
  }
  function apiHeaders() {
    var key = getKey();
    var h = { 'Content-Type': 'application/json' };
    if (key) h['X-Admin-Key'] = key;
    return h;
  }
  function setAuthStatus(ok, msg) {
    var text = msg || (ok ? 'Key set' : 'Set key for API');
    var color = ok ? '#0a7' : '#888';
    var el = document.getElementById('authStatus');
    if (el) { el.textContent = text; el.style.color = color; }
    var el2 = document.getElementById('adminAuthStatus');
    if (el2) { el2.textContent = text; el2.style.color = color; }
  }
  var adminKeyEl = document.getElementById('adminKey');
  if (adminKeyEl) adminKeyEl.value = getKey();
  var setKeyBtn = document.getElementById('setKey');
  if (setKeyBtn) setKeyBtn.addEventListener('click', function () {
    var inp = document.getElementById('adminKey');
    var key = inp ? inp.value.trim() : '';
    if (key) { localStorage.setItem('proxy_admin_key', key); setAuthStatus(true, 'Key set'); }
  });
  setAuthStatus(!!getKey());

  /** Replace window.confirm (unreliable on some mobile browsers) with an inline modal */
  function openConfirmModal(message, onConfirm) {
    var modal = document.getElementById('confirmModal');
    var msgEl = document.getElementById('confirmMessage');
    var ok = document.getElementById('confirmOk');
    var cancel = document.getElementById('confirmCancel');
    if (!modal || !msgEl || !ok || !cancel) {
      if (window.confirm(message)) onConfirm();
      return;
    }
    msgEl.textContent = message;
    modal.classList.remove('hidden');
    function cleanup() {
      modal.classList.add('hidden');
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
      modal.removeEventListener('click', onBackdrop);
    }
    function onOk() {
      cleanup();
      onConfirm();
    }
    function onCancel() {
      cleanup();
    }
    function onBackdrop(e) {
      if (e.target === modal) onCancel();
    }
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
    modal.addEventListener('click', onBackdrop);
  }

  /* ---------- Helpers ---------- */
  function escapeHtml(s) {
    if (s == null) return '';
    var d = document.createElement('div'); d.textContent = s; return d.innerHTML;
  }
  function renderMarkdown(text) {
    if (!text) return '';
    if (typeof window.marked !== 'undefined' && window.marked.parse) {
      try { window.marked.setOptions({ gfm: true, breaks: true }); return window.marked.parse(String(text)); } catch (_) {}
    }
    return '<pre>' + escapeHtml(text) + '</pre>';
  }
  function fmtDuration(v) { return v != null ? v.toFixed(2) + 's' : ''; }
  function fmtDurationShort(v) { return v != null ? Math.round(v) + 's' : '—'; }
  function isEmptyPrompt(p) { return !p || p === 'N/A'; }

  function renderToolCallsHtml(toolCallsJson) {
    if (!toolCallsJson) return '';
    var calls;
    try { calls = JSON.parse(toolCallsJson); } catch (_) { return ''; }
    if (!Array.isArray(calls) || calls.length === 0) return '';
    var inner = calls.map(function (tc) {
      var fn = (tc.function || tc);
      var name = fn.name || '?';
      var args = fn.arguments || '';
      var prettyArgs = '';
      try { prettyArgs = JSON.stringify(JSON.parse(args), null, 2); } catch (_) { prettyArgs = args; }
      return '<div class="tool-call-item"><span class="tool-call-badge">' + escapeHtml(name) + '</span>' +
        (prettyArgs ? '<pre class="tool-call-args">' + escapeHtml(prettyArgs) + '</pre>' : '') + '</div>';
    }).join('');
    return '<details class="thread-tool-calls"><summary>Tool Calls (' + calls.length + ')</summary>' + inner + '</details>';
  }

  function renderFinishReasonBadge(reason) {
    if (!reason) return '';
    var cls = 'finish-reason';
    if (reason === 'stop') cls += ' finish-stop';
    else if (reason === 'tool_calls') cls += ' finish-tool-calls';
    else if (reason === 'length') cls += ' finish-length';
    return ' <span class="' + cls + '">' + escapeHtml(reason) + '</span>';
  }

  /* ---------- Tabs ---------- */
  document.querySelectorAll('.tabs button').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tab = btn.getAttribute('data-tab');
      document.querySelectorAll('.tabs button').forEach(function (b) { b.classList.remove('active'); });
      document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.add('hidden'); });
      btn.classList.add('active');
      var panel = document.getElementById(tab);
      if (panel) panel.classList.remove('hidden');
      // Auto-refresh data when switching tabs
      if (tab === 'home' && getKey()) loadHome();
      if (tab === 'conversations' && getKey()) loadSessions();
      if (tab === 'history' && getKey()) loadHistory(false);
      if (tab === 'histogram') loadHistogram();
    });
  });
  document.querySelector('.tabs button[data-tab="home"]').classList.add('active');
  document.getElementById('conversations').classList.add('hidden');
  document.getElementById('history').classList.add('hidden');
  document.getElementById('histogram').classList.add('hidden');
  document.getElementById('admin').classList.add('hidden');

  /* ================================================================
     HOME (dashboard overview)
     ================================================================ */
  var HOME_DISPLAY_LIMIT = 10;
  var HOME_RECENT_LIMIT = 5;
  /** Narrow columns for faster /proxy/query_db (skip large TEXT blobs). */
  var FIELDS_HOME_RECENT = 'request_id,model,ip_address,status,duration_seconds,processing_time_seconds,queue_wait_seconds,timestamp_received,timestamp_completed,session_id,endpoint';
  var DEBOUNCE_MS = 150;
  /** Request History tab: offset for /proxy/query_db pagination */
  var historyPageOffset = 0;
  /** Conversations tab: offset for /proxy/conversation_sessions */
  var convPageOffset = 0;
  /** Min interval between WebSocket-triggered home refreshes (reduces API storm under load). */
  var WS_HOME_THROTTLE_MS = 2500;
  var lastWsHomeRefresh = 0;
  var homeDebounceTimer = null;
  var sessionsDebounceTimer = null;
  function getActiveTabId() {
    var active = document.querySelector('.tabs button.active');
    return active ? active.getAttribute('data-tab') : '';
  }
  function throttledLoadHomeFromWs() {
    if (getActiveTabId() !== 'home') return;
    var now = Date.now();
    if (now - lastWsHomeRefresh < WS_HOME_THROTTLE_MS) return;
    lastWsHomeRefresh = now;
    debouncedLoadHome();
  }
  function debouncedLoadHome() {
    if (homeDebounceTimer) clearTimeout(homeDebounceTimer);
    homeDebounceTimer = setTimeout(function () { homeDebounceTimer = null; loadHome(); }, DEBOUNCE_MS);
  }
  function debouncedLoadSessions() {
    if (sessionsDebounceTimer) clearTimeout(sessionsDebounceTimer);
    sessionsDebounceTimer = setTimeout(function () { sessionsDebounceTimer = null; loadSessions(); }, DEBOUNCE_MS);
  }

  function renderHealth(data) {
    var el = document.getElementById('homeHealth');
    if (!el) return;
    if (!data || data.error) {
      el.innerHTML = '<div class="dash-status dash-status-error">ERROR</div><div class="dash-kv"><span class="dash-kv-key">Message</span><span>' + escapeHtml(data && data.error ? data.error : 'No Data') + '</span></div>';
      return;
    }
    var status = (data.status || 'unknown').toUpperCase();
    var paused = data.paused ? ' [PAUSED]' : '';
    var statusClass = (data.status === 'healthy' && !data.paused) ? 'dash-status-ok' : 'dash-status-error';
    var active = (data.active_requests != null ? data.active_requests : 0) + '/' + (data.max_parallel != null ? data.max_parallel : 0);
    var queue = data.queue_depth != null ? data.queue_depth : 0;
    var total = (data.stats && data.stats.total_requests != null) ? data.stats.total_requests : 0;
    el.innerHTML =
      '<div class="dash-status ' + statusClass + '">' + escapeHtml(status) + escapeHtml(paused) + '</div>' +
      '<div class="dash-kv"><span class="dash-kv-key">Active</span><span>' + escapeHtml(String(active)) + '</span></div>' +
      '<div class="dash-kv"><span class="dash-kv-key">Queue</span><span>' + escapeHtml(String(queue)) + '</span></div>' +
      '<div class="dash-kv"><span class="dash-kv-key">Total</span><span>' + escapeHtml(String(total)) + '</span></div>';
  }

  function renderVram(data) {
    var el = document.getElementById('homeVram');
    if (!el) return;
    if (!data || data.error) {
      el.innerHTML = '<div class="dash-muted">N/A</div>';
      return;
    }
    var totalMb = data.total_vram_used_mb != null ? data.total_vram_used_mb : 0;
    var totalGb = (totalMb / 1024).toFixed(1);
    var models = data.models || {};
    var rows = Object.keys(models).slice(0, 5).map(function (m) {
      var info = models[m];
      var mb = (info && typeof info === 'object' && info.vram_mb != null) ? info.vram_mb : 0;
      var gb = (mb / 1024).toFixed(1);
      return '<tr><td>' + escapeHtml(String(m).slice(0, 40)) + '</td><td class="dash-num">' + gb + ' GB</td></tr>';
    }).join('');
    el.innerHTML = '<div class="dash-kv"><span class="dash-kv-key">Total</span><span>' + totalGb + ' GB Used</span></div><table class="dash-table"><thead><tr><th>Model</th><th>Size</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function renderQueue(data) {
    var el = document.getElementById('homeQueue');
    if (!el) return;
    if (!data || data.error) {
      el.innerHTML = '<div class="dash-muted">Error</div>';
      return;
    }
    var reqs = data.requests || [];
    reqs.sort(function (a, b) {
      var ap = a.status === 'processing' ? 0 : 1;
      var bp = b.status === 'processing' ? 0 : 1;
      if (ap !== bp) return ap - bp;
      return (b.priority != null ? b.priority : 999) - (a.priority != null ? a.priority : 999);
    });
    if (reqs.length === 0) {
      el.innerHTML = '<div class="dash-muted">Queue Empty</div>';
      return;
    }
    var rows = reqs.slice(0, 40).map(function (r) {
      var icon = r.status === 'processing' ? '&#9889;' : '&#9201;';
      var dur = r.total_duration != null ? r.total_duration : r.wait_time;
      var durStr = (dur != null && !isNaN(parseFloat(dur))) ? parseFloat(dur).toFixed(1) + 's' : '0s';
      var rid = String(r.request_id || '');
      var act = r.status === 'processing' ? 'stop' : 'cancel';
      var btnLabel = act === 'stop' ? 'Stop' : 'Cancel';
      var btn = getKey()
        ? '<button type="button" class="queue-action-btn admin-btn admin-btn-danger" style="padding:0.2rem 0.45rem;font-size:0.75rem" data-rid="' + escapeHtml(rid) + '" data-act="' + act + '">' + btnLabel + '</button>'
        : '';
      return '<tr><td class="dash-icon">' + icon + '</td><td>' + escapeHtml(String(r.model || '?').slice(0, 36)) + '</td><td class="dash-dim">' + escapeHtml(String(r.ip || '?').slice(0, 12)) + '</td><td class="dash-num">' + durStr + '</td><td class="dash-queue-act">' + btn + '</td></tr>';
    }).join('');
    el.innerHTML = '<table class="dash-table"><thead><tr><th>St</th><th>Model</th><th>IP</th><th>Time</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>';
    el.onclick = function (ev) {
      var btn = ev.target.closest && ev.target.closest('.queue-action-btn');
      if (!btn) return;
      var rid = btn.getAttribute('data-rid');
      var act = btn.getAttribute('data-act');
      if (!rid || !getKey()) return;
      ev.preventDefault();
      var url = act === 'stop'
        ? API_BASE + '/stop-request/' + encodeURIComponent(rid)
        : API_BASE + '/cancel-request/' + encodeURIComponent(rid);
      adminPost(url, undefined, function () { loadHome(); });
    };
  }

  function renderRecent(data) {
    var el = document.getElementById('homeRecent');
    if (!el) return;
    var card = el.closest('.dash-card');
    var titleEl = card ? card.querySelector('.dash-card-title') : null;
    var totalCount = (data && data.total_count != null) ? data.total_count : 0;
    var setTitle = function (shown, total) {
      if (titleEl) titleEl.textContent = 'Recent (' + shown + '/' + total + ')';
    };
    if (!data || data.error) {
      setTitle(0, totalCount);
      el.innerHTML = '<div class="dash-muted">No Data</div>';
      return;
    }
    var recent = data.requests || [];
    var shown = Math.min(recent.length, HOME_RECENT_LIMIT);
    setTitle(shown, totalCount);
    var rows = recent.slice(0, HOME_RECENT_LIMIT).map(function (r) {
      var status = r.status || '';
      var icon = status === 'completed' || status === 'success' ? '&#10003;' : (status === 'error' || status === 'failed' ? '&#10007;' : '?');
      var iconClass = status === 'completed' || status === 'success' ? 'dash-icon-ok' : (status === 'error' || status === 'failed' ? 'dash-icon-err' : 'dash-icon-warn');
      var dur = r.processing_time_seconds;
      var durStr = (dur != null && !isNaN(parseFloat(dur))) ? parseFloat(dur).toFixed(1) + 's' : '0.0s';
      return '<tr><td class="dash-icon ' + iconClass + '">' + icon + '</td><td>' + escapeHtml(String(r.model || '?').slice(0, 40)) + '</td><td class="dash-dim">' + escapeHtml(String(r.ip_address || '?').slice(0, 15)) + '</td><td class="dash-num">' + durStr + '</td></tr>';
    }).join('');
    if (rows.length === 0) {
      el.innerHTML = '<div class="dash-muted">No Recent Requests</div>';
      return;
    }
    el.innerHTML = '<table class="dash-table"><thead><tr><th>St</th><th>Model</th><th>IP</th><th>Time</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function renderTopModels(data) {
    var el = document.getElementById('homeTopModels');
    if (!el) return;
    var list = (data && data.request_count_by_model) ? data.request_count_by_model : [];
    var rows = list.slice(0, HOME_DISPLAY_LIMIT).map(function (x) {
      return '<tr><td>' + escapeHtml(String(x.model || '?').slice(0, 40)) + '</td><td class="dash-num">' + (x.request_count != null ? x.request_count : 0) + '</td></tr>';
    }).join('');
    el.innerHTML = rows ? '<table class="dash-table"><thead><tr><th>Name</th><th>Reqs</th></tr></thead><tbody>' + rows + '</tbody></table>' : '<div class="dash-muted">No data</div>';
  }

  function renderTopIps(data) {
    var el = document.getElementById('homeTopIps');
    if (!el) return;
    var list = (data && data.request_count_by_ip) ? data.request_count_by_ip : [];
    var rows = list.slice(0, HOME_DISPLAY_LIMIT).map(function (x) {
      return '<tr><td>' + escapeHtml(x.ip_address || '?') + '</td><td class="dash-num">' + (x.request_count != null ? x.request_count : 0) + '</td></tr>';
    }).join('');
    el.innerHTML = rows ? '<table class="dash-table"><thead><tr><th>IP</th><th>Reqs</th></tr></thead><tbody>' + rows + '</tbody></table>' : '<div class="dash-muted">No data</div>';
  }

  function renderPerfModel(data) {
    var el = document.getElementById('homePerfModel');
    if (!el) return;
    var list = (data && data.perf_by_model) ? data.perf_by_model : [];
    var rows = list.slice(0, HOME_DISPLAY_LIMIT).map(function (x) {
      var w = x.avg_wait_seconds != null ? x.avg_wait_seconds : 0;
      var p = x.avg_processing_seconds != null ? x.avg_processing_seconds : 0;
      return '<tr><td>' + escapeHtml(String(x.group || '?').slice(0, 40)) + '</td><td class="dash-num">' + w.toFixed(1) + 's</td><td class="dash-num">' + p.toFixed(1) + 's</td></tr>';
    }).join('');
    el.innerHTML = rows ? '<table class="dash-table"><thead><tr><th>Name</th><th>Q Wait</th><th>Run</th></tr></thead><tbody>' + rows + '</tbody></table>' : '<div class="dash-muted">No data</div>';
  }

  function renderPerfIp(data) {
    var el = document.getElementById('homePerfIp');
    if (!el) return;
    var list = (data && data.perf_by_ip) ? data.perf_by_ip : [];
    var rows = list.slice(0, HOME_DISPLAY_LIMIT).map(function (x) {
      var w = x.avg_wait_seconds != null ? x.avg_wait_seconds : 0;
      var p = x.avg_processing_seconds != null ? x.avg_processing_seconds : 0;
      return '<tr><td>' + escapeHtml(String(x.group || '?')) + '</td><td class="dash-num">' + w.toFixed(1) + 's</td><td class="dash-num">' + p.toFixed(1) + 's</td></tr>';
    }).join('');
    el.innerHTML = rows ? '<table class="dash-table"><thead><tr><th>IP</th><th>Q Wait</th><th>Run</th></tr></thead><tbody>' + rows + '</tbody></table>' : '<div class="dash-muted">No data</div>';
  }

  function renderErrorsModel(data) {
    var el = document.getElementById('homeErrorsModel');
    if (!el) return;
    var list = (data && data.error_rate_analysis) ? data.error_rate_analysis : [];
    var rows = list.slice(0, HOME_DISPLAY_LIMIT).map(function (x) {
      var pct = x.error_rate_percent != null ? x.error_rate_percent : 0;
      return '<tr><td>' + escapeHtml(String(x.group || '?').slice(0, 40)) + '</td><td class="dash-num">' + pct.toFixed(1) + '%</td></tr>';
    }).join('');
    el.innerHTML = rows ? '<table class="dash-table"><thead><tr><th>Name</th><th>%</th></tr></thead><tbody>' + rows + '</tbody></table>' : '<div class="dash-muted">No data</div>';
  }

  function renderErrorsIp(data) {
    var el = document.getElementById('homeErrorsIp');
    if (!el) return;
    var list = (data && data.error_rate_by_ip) ? data.error_rate_by_ip : [];
    var rows = list.slice(0, HOME_DISPLAY_LIMIT).map(function (x) {
      var pct = x.error_rate_percent != null ? x.error_rate_percent : 0;
      return '<tr><td>' + escapeHtml(String(x.group || '?').slice(0, 15)) + '</td><td class="dash-num">' + pct.toFixed(1) + '%</td></tr>';
    }).join('');
    el.innerHTML = rows ? '<table class="dash-table"><thead><tr><th>IP</th><th>%</th></tr></thead><tbody>' + rows + '</tbody></table>' : '<div class="dash-muted">No data</div>';
  }

  function loadHome() {
    var key = getKey();
    var hours = parseInt(document.getElementById('homeHours').value, 10) || 72;
    var fromTime = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
    var healthPromise = fetch(API_BASE + '/health', { headers: apiHeaders() }).then(function (r) { return r.json(); }).catch(function (e) { return { error: String(e.message || e) }; });
    var queuePromise = fetch(API_BASE + '/queue', { headers: apiHeaders() }).then(function (r) { return r.json(); }).catch(function (e) { return { error: String(e.message || e) }; });
    var vramPromise = fetch(API_BASE + '/vram', { headers: apiHeaders() }).then(function (r) { return r.json(); }).catch(function (e) { return { error: String(e.message || e) }; });
    var analyticsPromise = key
      ? fetch(API_BASE + '/analytics?hours=' + encodeURIComponent(hours) + '&limit=' + HOME_DISPLAY_LIMIT, { headers: apiHeaders() }).then(function (r) { if (r.status === 403) throw new Error('Forbidden'); return r.json(); }).catch(function (e) { return { error: String(e.message || e) }; })
      : Promise.resolve({ error: 'Set key for analytics' });
    var recentPromise = key
      ? fetch(API_BASE + '/query_db?limit=' + HOME_RECENT_LIMIT + '&status=completed,error&sort_by=timestamp_completed&sort_order=desc&from_time=' + encodeURIComponent(fromTime) + '&fields=' + encodeURIComponent(FIELDS_HOME_RECENT), { headers: apiHeaders() }).then(function (r) { if (r.status === 403) throw new Error('Forbidden'); return r.json(); }).catch(function (e) { return { error: String(e.message || e) }; })
      : Promise.resolve({ error: 'Set key for recent' });
    Promise.all([healthPromise, queuePromise, vramPromise, analyticsPromise, recentPromise]).then(function (results) {
      var health = results[0];
      var queue = results[1];
      var vram = results[2];
      var analytics = results[3];
      var recent = results[4];
      renderHealth(health);
      renderVram(vram);
      renderQueue(queue);
      renderRecent(recent);
      if (analytics && !analytics.error) {
        renderTopModels(analytics);
        renderTopIps(analytics);
        renderPerfModel(analytics);
        renderPerfIp(analytics);
        renderErrorsModel(analytics);
        renderErrorsIp(analytics);
      } else {
        ['homeTopModels', 'homeTopIps', 'homePerfModel', 'homePerfIp', 'homeErrorsModel', 'homeErrorsIp'].forEach(function (id) {
          var e = document.getElementById(id);
          if (e) e.innerHTML = '<div class="dash-muted">' + (analytics && analytics.error ? escapeHtml(analytics.error) : 'Set key') + '</div>';
        });
      }
      var lastEl = document.getElementById('homeLastUpdated');
      if (lastEl) lastEl.textContent = new Date().toLocaleTimeString();
    });
  }

  document.getElementById('loadHome').addEventListener('click', function () { loadHome(); });
  if (getKey()) loadHome();

  /* ================================================================
     CONVERSATIONS (with embedded live) — shared WebSocket, event-driven refresh
     ================================================================ */
  var ws = null;
  var wsStatusEl = document.getElementById('wsStatus');
  var homeWsStatusEl = document.getElementById('homeWsStatus');
  var convWsIndicator = document.getElementById('convWsIndicator');
  var liveMode = false; // set by "Go live" / "Stop live" (UI); chunk rendering no longer depends on it
  var liveAccumulated = {};
  var liveThinkingAccumulated = {};
  var reconnectDelay = 2000;
  var maxReconnectDelay = 30000;
  var reconnectTimer = null;
  var intentionalDisconnect = false;

  function setWsStatus(text, color) {
    if (wsStatusEl) { wsStatusEl.textContent = text; wsStatusEl.style.color = color || ''; }
    if (homeWsStatusEl) { homeWsStatusEl.textContent = text; homeWsStatusEl.style.color = color || ''; }
    if (convWsIndicator) { convWsIndicator.textContent = text ? ('\u25CF ' + text) : ''; convWsIndicator.style.color = color || ''; }
  }

  function buildLiveWsUrl() {
    var key = getKey();
    if (!key) return null;
    var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return proto + '//' + window.location.host + API_BASE + '/live?key=' + encodeURIComponent(key);
  }

  /** Subscribe-on-open: the server only streams chunk content for the conversation we subscribe to. */
  function subscribeWs(conversationKey) {
    if (ws && ws.readyState === WebSocket.OPEN && conversationKey) {
      try { ws.send(JSON.stringify({ type: 'subscribe', conversation_key: conversationKey })); } catch (_) {}
    }
  }
  function unsubscribeWs() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: 'unsubscribe' })); } catch (_) {}
    }
  }

  function connectLiveWs() {
    var url = buildLiveWsUrl();
    if (!url) return;
    if (ws && ws.readyState === WebSocket.OPEN) return;
    intentionalDisconnect = false;
    ws = new WebSocket(url);
    setWsStatus('connecting…');
    ws.onopen = function () {
      reconnectDelay = 2000;
      setWsStatus('live', '#0f0');
      document.getElementById('connectWs').classList.add('hidden');
      document.getElementById('disconnectWs').classList.remove('hidden');
      // Re-subscribe to the open conversation after a reconnect so its stream resumes.
      if (currentConvKey) subscribeWs(currentConvKey);
    };
    ws.onmessage = function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        if (msg.type === 'request_queued' || msg.type === 'request_processing' || msg.type === 'request_started' || msg.type === 'request_completed') {
          throttledLoadHomeFromWs();
        }
        if (msg.type === 'request_started') {
          // Lightweight list refresh only (no content) while browsing conversations.
          if (getActiveTabId() === 'conversations') debouncedLoadSessions();
          // A new turn in the OPEN conversation → adopt the new (fuller) canonical.
          var ck = msg.metadata && msg.metadata.conversation_key;
          if (ck && currentConvKey && ck === currentConvKey) {
            debouncedRefreshOpenConversation();
          }
        } else if (msg.type === 'request_completed') {
          // Finalize live text into completed state for the open conversation only.
          if (liveAccumulated[msg.request_id] || findAssistantDiv(msg.request_id)) {
            finalizeCompletedRequest(msg.request_id);
          }
          if (getActiveTabId() === 'conversations') debouncedLoadSessions();
        } else if (msg.type === 'chunk') {
          // Chunks are only sent by the server for the conversation we subscribed to (the open one).
          var kind = msg.kind || 'content';
          var fullText = msg.full !== undefined ? msg.full : ((liveAccumulated[msg.request_id] || '') + (msg.delta || ''));
          var fullThinking = msg.full_thinking !== undefined ? msg.full_thinking : ((liveThinkingAccumulated[msg.request_id] || '') + (kind === 'thinking' ? (msg.delta || '') : ''));
          liveAccumulated[msg.request_id] = fullText;
          liveThinkingAccumulated[msg.request_id] = fullThinking;
          var fullToolCalls = msg.full_tool_calls || '';
          pendingChunks[msg.request_id] = { kind: kind, fullText: fullText, fullThinking: fullThinking, fullToolCalls: fullToolCalls };
          if (!chunkRAF) {
            chunkRAF = requestAnimationFrame(flushChunkUpdates);
          }
        }
      } catch (_) {}
    };
    ws.onclose = function () {
      ws = null;
      setWsStatus('');
      document.getElementById('connectWs').classList.remove('hidden');
      document.getElementById('disconnectWs').classList.add('hidden');
      if (!intentionalDisconnect && getKey()) {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(function () {
          reconnectTimer = null;
          connectLiveWs();
          reconnectDelay = Math.min(reconnectDelay * 2, maxReconnectDelay);
        }, reconnectDelay);
      }
    };
    ws.onerror = function () { setWsStatus('error', '#f44'); };
  }

  document.getElementById('connectWs').addEventListener('click', function () {
    if (!getKey()) { setAuthStatus(false, 'Set key first'); return; }
    liveMode = true;
    connectLiveWs();
  });
  document.getElementById('disconnectWs').addEventListener('click', function () {
    liveMode = false;
    intentionalDisconnect = true;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
  });

  if (getKey()) connectLiveWs();

  /* ---------- RAF chunk batching ---------- */
  var pendingChunks = {};
  var chunkRAF = null;

  /** Insert thinking block (details/pre) before the assistant body if missing (DB has no thinking_text until stream end). */
  function ensureThinkingBlock(row) {
    if (row.querySelector('.thread-thinking .streamable-thinking')) return;
    var bodyEl = row.querySelector('.body');
    if (!bodyEl) return;
    var details = document.createElement('details');
    details.className = 'thread-thinking thread-thinking-live';
    details.setAttribute('open', 'open');
    details.innerHTML = '<summary>Thinking</summary><pre class="thread-thinking-body streamable-thinking"></pre>';
    row.insertBefore(details, bodyEl);
  }

  /** Keep the scrollable thinking <pre> pinned to the latest token (max-height + overflow-y: auto in CSS). */
  function scrollThinkingPreToBottom(preEl) {
    if (!preEl) return;
    preEl.scrollTop = preEl.scrollHeight;
  }

  function flushChunkUpdates() {
    chunkRAF = null;
    var needScroll = false;
    var ids = Object.keys(pendingChunks);
    for (var i = 0; i < ids.length; i++) {
      var rid = ids[i];
      var info = pendingChunks[rid];
      var row = findAssistantDiv(rid);
      if (row) {
        if (info.kind === 'thinking') {
          ensureThinkingBlock(row);
          var thinkingPre = row.querySelector('.thread-thinking .streamable-thinking');
          if (thinkingPre) {
            thinkingPre.textContent = info.fullThinking;
            scrollThinkingPreToBottom(thinkingPre);
          }
          var thinkingDetails = row.querySelector('.thread-thinking');
          if (thinkingDetails) thinkingDetails.setAttribute('open', 'open');
        } else {
          /* Content chunks still carry full_thinking; keep the reasoning pane in sync and scrolled. */
          if (info.fullThinking && String(info.fullThinking).length > 0) {
            ensureThinkingBlock(row);
            var thPre = row.querySelector('.thread-thinking .streamable-thinking');
            if (thPre) {
              thPre.textContent = info.fullThinking;
              scrollThinkingPreToBottom(thPre);
            }
          }
          var streamEl = row.querySelector('.body.streamable');
          if (streamEl) streamEl.textContent = info.fullText;
          if (info.fullToolCalls) {
            var existingTc = row.querySelector('.thread-tool-calls');
            var newTcHtml = renderToolCallsHtml(info.fullToolCalls);
            if (newTcHtml) {
              if (existingTc) { existingTc.outerHTML = newTcHtml; }
              else {
                var bodyEl2 = row.querySelector('.body');
                if (bodyEl2) bodyEl2.insertAdjacentHTML('afterend', newTcHtml);
              }
            }
          }
        }
        // Chunks only arrive for the open conversation, so a matching row means we should follow it.
        needScroll = true;
      }
    }
    pendingChunks = {};
    if (needScroll) scrollThreadToBottom();
  }

  /* ---------- Persist user settings (localStorage) ---------- */
  var homeHoursEl = document.getElementById('homeHours');
  if (homeHoursEl) {
    var savedHomeHours = localStorage.getItem('proxy_home_hours');
    if (savedHomeHours !== null) homeHoursEl.value = savedHomeHours;
    homeHoursEl.addEventListener('change', function () { localStorage.setItem('proxy_home_hours', this.value); });
  }
  var convLimitEl = document.getElementById('convLimit');
  if (convLimitEl) {
    var savedConvLimit = localStorage.getItem('proxy_conv_limit');
    if (savedConvLimit !== null) convLimitEl.value = savedConvLimit;
    convLimitEl.addEventListener('change', function () { localStorage.setItem('proxy_conv_limit', this.value); });
  }
  var historyLimitEl = document.getElementById('limit');
  if (historyLimitEl) {
    var savedHistoryLimit = localStorage.getItem('proxy_history_limit');
    if (savedHistoryLimit !== null) historyLimitEl.value = savedHistoryLimit;
    historyLimitEl.addEventListener('change', function () { localStorage.setItem('proxy_history_limit', this.value); });
  }

  /** After request_completed for the open conversation: finalize live text in-place, then refresh
   *  the open conversation (to adopt final metadata / next canonical) and the list badges. */
  function finalizeCompletedRequest(requestId) {
    var finalText = liveAccumulated[requestId] || '';
    delete liveAccumulated[requestId];
    delete liveThinkingAccumulated[requestId];
    // Update the DOM in-place: swap streaming indicator for final state.
    var row = findAssistantDiv(requestId);
    if (row) {
      row.classList.remove('thread-msg-live');
      var indicator = row.querySelector('.streaming-indicator');
      if (indicator) indicator.textContent = 'done';
      var streamEl = row.querySelector('.body.streamable');
      if (streamEl && finalText) {
        streamEl.className = 'body markdown-body';
        streamEl.innerHTML = renderMarkdown(finalText);
      }
    }
    // Re-fetch the open conversation so finish reason + any new canonical render correctly.
    if (currentConvKey) debouncedRefreshOpenConversation();
    if (getActiveTabId() === 'conversations') debouncedLoadSessions();
  }

  function findAssistantDiv(requestId) {
    if (!requestId) return null;
    var mapped = assistantRowByRid[requestId];
    if (mapped && mapped.isConnected) return mapped;
    var divs = document.querySelectorAll('#threadMessages .thread-msg[data-request-id]');
    for (var i = 0; i < divs.length; i++) {
      if (divs[i].getAttribute('data-request-id') === requestId) return divs[i];
    }
    return null;
  }

  /** Scroll the conversation thread to the bottom so the latest content is visible. */
  function scrollThreadToBottom() {
    var container = document.getElementById('threadMessages');
    var threadPanel = document.getElementById('sessionThread');
    if (!container || !threadPanel || threadPanel.classList.contains('hidden')) return;
    var last = container.lastElementChild;
    if (last) last.scrollIntoView({ block: 'end', behavior: 'auto' });
  }

  /* -- Sessions list -- */
  var currentConvKey = null;   // conversation_key currently open, or null (list view)
  var currentConvData = null;  // last /conversation_thread payload for the open conversation
  /** O(1) lookup for assistant rows while a thread is open (avoids repeated querySelectorAll per chunk). */
  var assistantRowByRid = {};

  /** Per-session manual collapse overrides: only stores keys the user explicitly toggled.
   *  { sid: { msgKey: true/false } } where true = collapsed.
   *  msgKey = request_id + ':user' | request_id + ':assistant' | 'system'. */
  var sessionCollapseState = {};

  function updateConvPager(data) {
    var pager = document.getElementById('convPager');
    var meta = document.getElementById('convPagerMeta');
    var prev = document.getElementById('convPrev');
    var next = document.getElementById('convNext');
    if (!pager || !meta) return;
    var total = data.total_count != null ? data.total_count : 0;
    var offset = data.offset != null ? data.offset : 0;
    var count = (data.sessions || []).length;
    if (total === 0 && count === 0) {
      pager.classList.add('hidden');
      return;
    }
    pager.classList.remove('hidden');
    var metaText;
    if (count === 0 && total > 0) {
      metaText = 'No conversations on this page — use Previous';
    } else {
      var start = total === 0 ? 0 : offset + 1;
      var end = offset + count;
      metaText = 'Showing ' + start + '–' + end + ' of ' + total + ' conversation(s)';
    }
    meta.textContent = metaText;
    if (prev) prev.disabled = offset <= 0;
    if (next) next.disabled = offset + count >= total;
  }

  /** Fetch a single conversation rendered from its canonical request(s). Only canonical bodies are
   *  returned by the server, so this stays small even for very long conversations. */
  function fetchConversationThread(conversationKey) {
    var key = getKey();
    if (!key) return Promise.reject(new Error('No key'));
    return fetch(API_BASE + '/conversation_thread?conversation_key=' + encodeURIComponent(conversationKey), { headers: apiHeaders() })
      .then(function (r) { if (r.status === 403) throw new Error('Forbidden'); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }

  /** Open a conversation: subscribe FIRST (so no chunks are missed), then load + render its content. */
  function openConversation(conversationKey, forceScroll) {
    if (!conversationKey) return;
    currentConvKey = conversationKey;
    subscribeWs(conversationKey);
    fetchConversationThread(conversationKey).then(function (data) {
      if (currentConvKey !== conversationKey) return; // user switched away meanwhile
      showSessionThread(conversationKey, data, forceScroll);
    }).catch(function (e) { alert('Failed to open conversation: ' + (e.message || e)); });
  }

  function refreshOpenConversation(forceScroll) {
    if (!currentConvKey) return;
    var ck = currentConvKey;
    fetchConversationThread(ck).then(function (data) {
      if (currentConvKey !== ck) return;
      showSessionThread(ck, data, forceScroll);
    }).catch(function () { /* keep existing thread on transient errors */ });
  }
  var refreshConvTimer = null;
  function debouncedRefreshOpenConversation() {
    if (refreshConvTimer) clearTimeout(refreshConvTimer);
    refreshConvTimer = setTimeout(function () { refreshConvTimer = null; refreshOpenConversation(false); }, DEBOUNCE_MS);
  }

  function loadSessions() {
    var key = getKey();
    if (!key) { setAuthStatus(false, 'Set key first'); return; }
    var limitEl = document.getElementById('convLimit');
    var limit = (limitEl && limitEl.value) ? parseInt(limitEl.value, 10) : 100;
    if (isNaN(limit) || limit < 1) limit = 100;
    if (limit > 500) limit = 500;
    var offset = convPageOffset;
    var modelEl = document.getElementById('convFilterModel');
    var ipEl = document.getElementById('convFilterIp');
    var model = modelEl && modelEl.value ? modelEl.value.trim() : '';
    var ip = ipEl && ipEl.value ? ipEl.value.trim() : '';
    var url = API_BASE + '/conversation_sessions?limit=' + limit + '&offset=' + offset;
    if (model) url += '&model=' + encodeURIComponent(model);
    if (ip) url += '&ip_address=' + encodeURIComponent(ip);
    fetch(url, { headers: apiHeaders() })
      .then(function (r) { if (r.status === 403) throw new Error('Forbidden'); return r.json(); })
      .then(function (data) {
        if (data.offset != null) convPageOffset = data.offset;
        updateConvPager(data);
        var listEl = document.getElementById('sessionList');
        listEl.innerHTML = '';
        (data.sessions || []).forEach(function (sess) {
          var ck = sess.conversation_key;
          var time = sess.last_timestamp_received ? new Date(sess.last_timestamp_received).toLocaleString() : '';
          var preview = (sess.preview_prompt || '').slice(0, 80);
          if (sess.preview_prompt && sess.preview_prompt.length > 80) preview += '…';
          var inProgress = !!sess.has_live;
          var item = document.createElement('div');
          item.className = 'session-item' + (inProgress ? ' session-live' : '');
          item.innerHTML =
            '<div class="session-header"><strong>' + escapeHtml(sess.model || '') + '</strong> · ' +
            (sess.message_count || 0) + ' msg · ' + (sess.turn_count || 0) + ' turn(s) · ' + escapeHtml(time) +
            (inProgress ? ' <span class="live-badge">live</span>' : '') +
            '</div><div class="session-preview">' + escapeHtml(preview) + '</div>';
          item.addEventListener('click', function () { openConversation(ck, true); });
          listEl.appendChild(item);
        });
      })
      .catch(function (e) { console.error('Load sessions failed:', e); });
  }
  document.getElementById('loadSessions').addEventListener('click', function () { convPageOffset = 0; loadSessions(); });
  ['convFilterModel', 'convFilterIp'].forEach(function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', function () { convPageOffset = 0; loadSessions(); });
    el.addEventListener('keydown', function (e) { if (e.key === 'Enter') { convPageOffset = 0; loadSessions(); } });
  });
  var convPrev = document.getElementById('convPrev');
  var convNext = document.getElementById('convNext');
  if (convPrev) {
    convPrev.addEventListener('click', function () {
      if (!getKey()) return;
      var limitEl = document.getElementById('convLimit');
      var lim = (limitEl && limitEl.value) ? parseInt(limitEl.value, 10) : 100;
      if (isNaN(lim) || lim < 1) lim = 100;
      if (lim > 500) lim = 500;
      convPageOffset = Math.max(0, convPageOffset - lim);
      loadSessions();
    });
  }
  if (convNext) {
    convNext.addEventListener('click', function () {
      if (!getKey()) return;
      var limitEl = document.getElementById('convLimit');
      var lim = (limitEl && limitEl.value) ? parseInt(limitEl.value, 10) : 100;
      if (isNaN(lim) || lim < 1) lim = 100;
      if (lim > 500) lim = 500;
      convPageOffset += lim;
      loadSessions();
    });
  }
  document.getElementById('backToSessions').addEventListener('click', function () {
    document.getElementById('sessionThread').classList.add('hidden');
    document.getElementById('sessionList').style.display = '';
    unsubscribeWs();
    currentConvKey = null;
    currentConvData = null;
    assistantRowByRid = {};
  });

  /* -- Thread view -- */

  /** Add a gutter + preview to a message div.  Clicking the gutter toggles collapsed state. */
  function addGutterAndPreview(msgDiv, msgKey, previewText, sid) {
    var gutter = document.createElement('div');
    gutter.className = 'thread-msg-gutter';
    gutter.title = 'Click to collapse/expand';
    msgDiv.insertBefore(gutter, msgDiv.firstChild);

    var preview = document.createElement('div');
    preview.className = 'thread-msg-preview';
    preview.textContent = (previewText || '').slice(0, 120);
    msgDiv.appendChild(preview);

    gutter.addEventListener('click', function (e) {
      e.stopPropagation();
      var isCollapsed = msgDiv.classList.toggle('collapsed');
      if (!sessionCollapseState[sid]) sessionCollapseState[sid] = {};
      sessionCollapseState[sid][msgKey] = isCollapsed;
    });
  }

  /** Extract displayable text from a message content field (string or OpenAI multimodal array). */
  function messageText(content) {
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content.map(function (it) {
        if (it && typeof it === 'object') {
          if (it.type === 'text') return it.text || '';
          if (it.type === 'image_url' || it.type === 'image') return '[image]';
          return '';
        }
        return typeof it === 'string' ? it : '';
      }).join('');
    }
    return content == null ? '' : String(content);
  }

  /** Default collapse state for a transcript message role (Copilot-style: history readable,
   *  system prompt + tool payloads tucked away). Manual gutter toggles override this. */
  function defaultCollapsedForRole(role) {
    return role === 'system' || role === 'tool';
  }

  /** Render one message from a canonical request's messages[] array into a thread-msg div. */
  function renderMessageBlock(ck, segIdx, mi, m, manualOverrides) {
    var role = (m && m.role) || '';
    var key = segIdx + ':' + mi + ':' + role;
    var text = messageText(m && m.content);
    var div = document.createElement('div');
    var roleLabel, cls, bodyHtml;
    if (role === 'system') {
      roleLabel = 'System'; cls = 'system';
      bodyHtml = '<div class="body">' + escapeHtml(text) + '</div>';
    } else if (role === 'assistant') {
      roleLabel = 'Assistant'; cls = '';
      var tc = (m && m.tool_calls) ? renderToolCallsHtml(JSON.stringify(m.tool_calls)) : '';
      var asstBody = text ? renderMarkdown(text) : '';
      bodyHtml = '<div class="body markdown-body">' + asstBody + '</div>' + tc;
    } else if (role === 'tool') {
      roleLabel = 'Tool Result'; cls = 'tool thread-msg-tool-turn';
      var callId = (m && m.tool_call_id) || 'result';
      bodyHtml = '<div class="tool-result-item"><span class="tool-call-badge tool-result-badge">' + escapeHtml(callId) +
        '</span><pre class="tool-call-args">' + escapeHtml(text) + '</pre></div>';
    } else {
      roleLabel = role === 'user' ? 'User' : (role || 'Message'); cls = 'user';
      bodyHtml = '<div class="body">' + escapeHtml(text) + '</div>';
    }
    var collapsed = (key in manualOverrides) ? manualOverrides[key] : defaultCollapsedForRole(role);
    div.className = 'thread-msg ' + cls + (collapsed ? ' collapsed' : '');
    div.innerHTML = '<div class="role">' + roleLabel + '</div>' + bodyHtml;
    addGutterAndPreview(div, key, text || roleLabel, ck);
    return div;
  }

  /** Render the canonical request's OWN assistant answer (the turn being generated / just completed).
   *  This turn is not part of messages[]; it streams live for an in-flight canonical. */
  function renderFinalAssistant(ck, segIdx, seg, model, manualOverrides) {
    var rid = seg.canonical_request_id || '';
    var isLive = (seg.canonical_status === 'processing' || seg.canonical_status === 'queued');
    var isError = seg.canonical_status === 'error';
    var key = segIdx + ':final';

    var respText, responseBody;
    if (liveAccumulated[rid]) {
      respText = liveAccumulated[rid];
      responseBody = escapeHtml(respText);
    } else {
      respText = seg.final_response_text || '';
      responseBody = (respText && (respText.indexOf('[HTTP') === 0 || respText.indexOf('[Error]') === 0))
        ? escapeHtml(respText) : renderMarkdown(respText);
    }

    var hasThinking = !!(seg.final_thinking_text && seg.final_thinking_text.trim());
    var liveThinking = isLive && (liveThinkingAccumulated[rid] || '');
    var thinkingHtml = '';
    if (hasThinking || liveThinking) {
      var thinkingContent = (seg.final_thinking_text && seg.final_thinking_text.trim()) || liveThinking || '';
      var thinkingOpen = isLive && liveThinking && !(liveAccumulated[rid] || responseBody);
      thinkingHtml = '<details class="thread-thinking' + (isLive ? ' thread-thinking-live' : '') + '"' + (thinkingOpen ? ' open' : '') +
        '><summary>Thinking</summary><pre class="thread-thinking-body' + (isLive ? ' streamable-thinking' : '') + '">' + escapeHtml(thinkingContent) + '</pre></details>';
    }

    var toolCallsHtml = renderToolCallsHtml(seg.final_tool_calls_json);
    var finishBadge = isLive ? '' : renderFinishReasonBadge(seg.final_finish_reason);
    var stopCtl = '';
    if (isLive && getKey() && rid) {
      var act = seg.canonical_status === 'queued' ? 'cancel' : 'stop';
      var label = act === 'cancel' ? 'Cancel' : 'Stop';
      stopCtl = ' <button type="button" class="conv-stop-btn admin-btn admin-btn-danger" style="padding:0.15rem 0.45rem;font-size:0.75rem" data-rid="' + escapeHtml(rid) + '" data-act="' + act + '">' + label + '</button>';
    }

    var div = document.createElement('div');
    var collapsed = (key in manualOverrides) ? manualOverrides[key] : false;
    div.className = 'thread-msg' + (isLive ? ' thread-msg-live' : '') + (isError ? ' thread-msg-error' : '') + (collapsed ? ' collapsed' : '');
    if (rid) { div.setAttribute('data-request-id', rid); assistantRowByRid[rid] = div; }
    div.innerHTML =
      '<div class="role">Assistant · ' + escapeHtml(model || '') +
      (isLive ? ' · <span class="streaming-indicator">streaming…</span>' : '') +
      finishBadge + stopCtl + '</div>' +
      thinkingHtml +
      '<div class="body ' + (isLive ? 'streamable' : 'markdown-body') + '">' + responseBody + '</div>' +
      toolCallsHtml;
    addGutterAndPreview(div, key, respText, ck);
    return div;
  }

  /**
   * Render a whole logical conversation from /conversation_thread as one instance: each segment's
   * canonical request contributes its full messages[] (the real system/user/assistant/tool thread)
   * plus that request's final assistant turn. Segments (context compaction) get a divider.
   */
  function showSessionThread(ck, data, forceScroll) {
    currentConvKey = ck;
    currentConvData = data;
    assistantRowByRid = {};
    document.getElementById('sessionList').style.display = 'none';
    document.getElementById('sessionThread').classList.remove('hidden');

    var segments = (data && data.segments) || [];
    var model = (data && data.model) || '';
    document.getElementById('sessionTitle').textContent =
      model + ' — ' + (data && data.message_count || 0) + ' message(s), ' + (data && data.turn_count || 0) + ' turn(s)';
    var container = document.getElementById('threadMessages');
    container.innerHTML = '';

    var manualOverrides = sessionCollapseState[ck] || {};
    var streaming = false;

    segments.forEach(function (seg, segIdx) {
      if (segIdx > 0) {
        var divider = document.createElement('div');
        divider.className = 'thread-segment-divider';
        divider.textContent = '— context compacted (new segment) —';
        container.appendChild(divider);
      }
      var msgs = seg.messages;
      if (msgs === null || msgs === undefined) {
        var warn = document.createElement('div');
        warn.className = 'thread-msg system';
        warn.innerHTML = '<div class="role">History unavailable</div>' +
          '<div class="body">This request body was not stored in full (a MAX_REQUEST_BODY_BYTES cap is set), so earlier messages cannot be shown.</div>';
        container.appendChild(warn);
      } else {
        msgs.forEach(function (m, mi) {
          container.appendChild(renderMessageBlock(ck, segIdx, mi, m, manualOverrides));
        });
      }
      if (seg.canonical_status === 'processing' || seg.canonical_status === 'queued') streaming = true;
      container.appendChild(renderFinalAssistant(ck, segIdx, seg, model, manualOverrides));
    });

    container.onclick = function (ev) {
      var btn = ev.target.closest && ev.target.closest('.conv-stop-btn');
      if (!btn) return;
      var rid = btn.getAttribute('data-rid');
      var act = btn.getAttribute('data-act');
      if (!rid || !getKey()) return;
      ev.preventDefault();
      var url = act === 'stop'
        ? API_BASE + '/stop-request/' + encodeURIComponent(rid)
        : API_BASE + '/cancel-request/' + encodeURIComponent(rid);
      adminPost(url, undefined, function () { loadSessions(); loadHome(); });
    };

    // Auto-scroll only on explicit open or when THIS conversation is streaming (never yank a
    // conversation the user is reading because a different one is streaming — chunks are gated
    // server-side to the open conversation anyway).
    if (forceScroll || streaming) {
      requestAnimationFrame(function () { scrollThreadToBottom(); });
    }
  }

  /* ================================================================
     HISTORY
     ================================================================ */
  function parseHistoryLimit() {
    var lim = parseInt(document.getElementById('limit').value, 10);
    if (isNaN(lim) || lim < 1) lim = 100;
    if (lim > 1000) lim = 1000;
    return lim;
  }

  function updateHistoryPager(data) {
    var pager = document.getElementById('historyPager');
    var meta = document.getElementById('historyPagerMeta');
    var prev = document.getElementById('historyPrev');
    var next = document.getElementById('historyNext');
    if (!pager || !meta) return;
    var total = data.total_count != null ? data.total_count : 0;
    var offset = data.offset != null ? data.offset : historyPageOffset;
    var count = (data.requests || []).length;
    if (total === 0 && count === 0) {
      pager.classList.add('hidden');
      return;
    }
    pager.classList.remove('hidden');
    var metaText;
    if (count === 0 && total > 0) {
      metaText = 'No requests on this page — use Previous';
    } else {
      var start = total === 0 ? 0 : offset + 1;
      var end = offset + count;
      metaText = 'Showing ' + start + '–' + end + ' of ' + total + ' request(s)';
    }
    meta.textContent = metaText;
    if (prev) prev.disabled = offset <= 0;
    if (next) next.disabled = offset + count >= total;
  }

  function loadHistory(resetOffset) {
    var key = getKey();
    var pager = document.getElementById('historyPager');
    if (!key) {
      if (pager) pager.classList.add('hidden');
      setAuthStatus(false, 'Set key first');
      return;
    }
    if (resetOffset) historyPageOffset = 0;
    var limit = parseHistoryLimit();
    var status = document.getElementById('filterStatus').value.trim();
    var model = document.getElementById('filterModel').value.trim();
    var ip = document.getElementById('filterIp').value.trim();
    var url = API_BASE + '/query_db?limit=' + encodeURIComponent(limit) +
      '&offset=' + encodeURIComponent(historyPageOffset) +
      '&sort_by=timestamp_received&sort_order=desc';
    if (status) url += '&status=' + encodeURIComponent(status);
    if (model) url += '&model=' + encodeURIComponent(model);
    if (ip) url += '&ip_address=' + encodeURIComponent(ip);
    fetch(url, { headers: apiHeaders() })
      .then(function (r) { if (r.status === 403) throw new Error('Forbidden'); return r.json(); })
      .then(function (data) {
        if (data.offset != null) historyPageOffset = data.offset;
        updateHistoryPager(data);
        var tbody = document.querySelector('#historyTable tbody');
        tbody.innerHTML = '';
        (data.requests || []).forEach(function (req) {
          var tr = document.createElement('tr');
          var time = req.timestamp_received ? new Date(req.timestamp_received).toLocaleString() : '';
          var toolIcon = req.tool_calls_json ? '<span class="tool-call-badge" title="Has tool calls">&#128295;</span>' : '';
          var frBadge = req.finish_reason ? renderFinishReasonBadge(req.finish_reason) : '';
          // Action button only for genuinely active rows: processing -> Stop, queued -> Cancel.
          var actCtl = '';
          if (getKey() && req.request_id) {
            if (req.status === 'processing') {
              actCtl = ' <button type="button" class="history-action-btn admin-btn admin-btn-danger" style="padding:0.15rem 0.45rem;font-size:0.75rem" data-rid="' + escapeHtml(req.request_id) + '" data-act="stop">Stop</button>';
            } else if (req.status === 'queued') {
              actCtl = ' <button type="button" class="history-action-btn admin-btn admin-btn-danger" style="padding:0.15rem 0.45rem;font-size:0.75rem" data-rid="' + escapeHtml(req.request_id) + '" data-act="cancel">Cancel</button>';
            }
          }
          tr.innerHTML =
            '<td><code>' + escapeHtml((req.request_id || '').slice(0, 12)) + '…</code></td>' +
            '<td>' + escapeHtml(time) + '</td>' +
            '<td>' + escapeHtml(req.model || '') + '</td>' +
            '<td>' + escapeHtml(req.ip_address || '') + '</td>' +
            '<td>' + escapeHtml(req.status || '') + frBadge + '</td>' +
            '<td>' + fmtDuration(req.duration_seconds) + '</td>' +
            '<td>' + fmtDuration(req.queue_wait_seconds) + '</td>' +
            '<td>' + fmtDuration(req.processing_time_seconds) + '</td>' +
            '<td>' + escapeHtml((req.session_id || '').slice(0, 16)) + '</td>' +
            '<td>' + escapeHtml((req.endpoint || '').replace(/^\/+/, '')) + toolIcon + '</td>' +
            '<td><a href="#" data-rid="' + escapeHtml(req.request_id) + '">Detail</a>' + actCtl + '</td>';
          tr.querySelector('a').addEventListener('click', function (e) { e.preventDefault(); openDetail(req.request_id); });
          var actBtn = tr.querySelector('.history-action-btn');
          if (actBtn) {
            actBtn.addEventListener('click', function (e) {
              e.preventDefault();
              var rid = actBtn.getAttribute('data-rid');
              var act = actBtn.getAttribute('data-act');
              if (!rid || !getKey()) return;
              var actUrl = act === 'stop'
                ? API_BASE + '/stop-request/' + encodeURIComponent(rid)
                : API_BASE + '/cancel-request/' + encodeURIComponent(rid);
              adminPost(actUrl, undefined, function () { loadHistory(false); });
            });
          }
          tbody.appendChild(tr);
        });
      })
      .catch(function (e) { alert('Load failed: ' + e.message); });
  }
  document.getElementById('loadHistory').addEventListener('click', function () { loadHistory(true); });
  var historyPrev = document.getElementById('historyPrev');
  var historyNext = document.getElementById('historyNext');
  if (historyPrev) {
    historyPrev.addEventListener('click', function () {
      if (!getKey()) return;
      var lim = parseHistoryLimit();
      historyPageOffset = Math.max(0, historyPageOffset - lim);
      loadHistory(false);
    });
  }
  if (historyNext) {
    historyNext.addEventListener('click', function () {
      if (!getKey()) return;
      var lim = parseHistoryLimit();
      historyPageOffset += lim;
      loadHistory(false);
    });
  }

  /* ================================================================
     DETAIL MODAL
     ================================================================ */
  function openDetail(requestId) {
    fetch(API_BASE + '/requests/' + encodeURIComponent(requestId), { headers: apiHeaders() })
      .then(function (r) {
        if (r.status === 403) throw new Error('Forbidden');
        if (r.status === 404) throw new Error('Not found');
        return r.json();
      })
      .then(function (req) {
        var metaRows = [
          ['Request ID', req.request_id], ['IP', req.ip_address], ['Model', req.model],
          ['Status', req.status], ['Finish Reason', req.finish_reason],
          ['Duration (s)', req.duration_seconds],
          ['Queue wait (s)', req.queue_wait_seconds], ['Processing (s)', req.processing_time_seconds],
          ['Input tokens', req.prompt_eval_count], ['Output tokens', req.eval_count],
          ['Priority score', req.priority_score], ['Session ID', req.session_id],
          ['Endpoint', req.endpoint], ['User-Agent', req.user_agent],
          ['Received', req.timestamp_received], ['Started', req.timestamp_started],
          ['Completed', req.timestamp_completed], ['Error', req.error_message],
          ['Tools available', req.tools_available]
        ].filter(function (r) { return r[1] != null && r[1] !== ''; });
        var metaHtml = '<table class="detail-meta-table"><tbody>';
        metaRows.forEach(function (r) { metaHtml += '<tr><td class="meta-key">' + escapeHtml(String(r[0])) + '</td><td>' + escapeHtml(String(r[1])) + '</td></tr>'; });
        metaHtml += '</tbody></table>';
        document.querySelector('.detail-meta').innerHTML = metaHtml;
        var detailParts = '';
        if (req.system_message) {
          detailParts += '--- System ---\n' + req.system_message + '\n\n';
        }
        detailParts += '--- Request (prompt) ---\n' + (req.prompt_text || '');
        if (req.thinking_text && req.thinking_text.trim()) {
          detailParts += '\n\n--- Thinking ---\n' + req.thinking_text.trim();
        }
        detailParts += '\n\n--- Response ---\n' + (req.response_text || '');
        if (req.tool_calls_json) {
          try {
            var tcPretty = JSON.stringify(JSON.parse(req.tool_calls_json), null, 2);
            detailParts += '\n\n--- Tool Calls ---\n' + tcPretty;
          } catch (_) {
            detailParts += '\n\n--- Tool Calls ---\n' + req.tool_calls_json;
          }
        }
        document.getElementById('detailText').textContent = detailParts;
        var rawContent;
        if (req.request_body && req.request_body.trim()) {
          try {
            rawContent = JSON.stringify(JSON.parse(req.request_body), null, 2);
          } catch (_) {
            rawContent = req.request_body;
          }
        } else {
          rawContent = JSON.stringify(req, null, 2);
        }
        document.getElementById('detailRaw').textContent = rawContent;
        document.getElementById('detailModal').classList.remove('hidden');
        document.getElementById('detailText').classList.remove('hidden');
        document.getElementById('detailRaw').classList.add('hidden');
        document.querySelectorAll('.detail-tabs button').forEach(function (b) {
          b.classList.toggle('active', b.getAttribute('data-detail') === 'text');
        });
      })
      .catch(function (e) { alert('Detail failed: ' + e.message); });
  }

  /* Detail tab switching */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.detail-tabs button');
    if (!btn) return;
    var which = btn.getAttribute('data-detail');
    if (!which) return;
    document.getElementById('detailText').classList.toggle('hidden', which !== 'text');
    document.getElementById('detailRaw').classList.toggle('hidden', which !== 'raw');
    document.querySelectorAll('.detail-tabs button').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-detail') === which);
    });
  });

  /* Close modal */
  document.getElementById('closeDetail').addEventListener('click', function () { document.getElementById('detailModal').classList.add('hidden'); });
  document.getElementById('detailModal').addEventListener('click', function (e) { if (e.target.id === 'detailModal') document.getElementById('detailModal').classList.add('hidden'); });

  /* ================================================================
     HISTOGRAM (precomputed rollups)
     ================================================================ */
  var histChartModel = null;
  var histChartIp = null;
  var HIST_HEIGHT_STORAGE = 'proxy_dashboard_hist_chart_height_px';
  var DEFAULT_HIST_HEIGHT = 560;
  var histChartHeightPx = DEFAULT_HIST_HEIGHT;
  var HIST_HEIGHT_MIN = 200;
  var HIST_HEIGHT_MAX = 900;

  function loadHistChartHeightFromStorage() {
    var saved = localStorage.getItem(HIST_HEIGHT_STORAGE);
    if (!saved) return;
    var n = parseInt(saved, 10);
    if (!isNaN(n) && n >= HIST_HEIGHT_MIN && n <= HIST_HEIGHT_MAX) histChartHeightPx = n;
  }

  function setHistChartHeightPx(px) {
    histChartHeightPx = Math.min(HIST_HEIGHT_MAX, Math.max(HIST_HEIGHT_MIN, Math.round(px)));
    localStorage.setItem(HIST_HEIGHT_STORAGE, String(histChartHeightPx));
    applyHistChartHeight();
  }

  function applyHistChartHeight() {
    var px = histChartHeightPx;
    if (typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 900px)').matches) {
      var vhCap = Math.floor((window.innerHeight || 600) * 0.42);
      var cap = Math.min(360, vhCap > 200 ? vhCap : 360);
      px = Math.min(px, cap);
      px = Math.max(HIST_HEIGHT_MIN, px);
    }
    document.querySelectorAll('.hist-canvas-wrap').forEach(function (w) {
      w.style.height = px + 'px';
    });
    if (histChartModel) histChartModel.resize();
    if (histChartIp) histChartIp.resize();
  }

  (function initHistChartResize() {
    loadHistChartHeightFromStorage();
    applyHistChartHeight();
    var handle = document.getElementById('histResizeHandle');
    if (!handle) return;
    var dragging = false;
    var startY = 0;
    var startH = 0;
    function onMove(e) {
      if (!dragging) return;
      var dy = e.clientY - startY;
      setHistChartHeightPx(startH + dy);
      e.preventDefault();
    }
    function onUp() {
      if (!dragging) return;
      dragging = false;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.removeEventListener('touchmove', onTouchMove);
      document.removeEventListener('touchend', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
    function onTouchMove(e) {
      if (!dragging || !e.touches || !e.touches[0]) return;
      var te = e.touches[0];
      var dy = te.clientY - startY;
      setHistChartHeightPx(startH + dy);
      e.preventDefault();
    }
    function startDrag(clientY) {
      dragging = true;
      startY = clientY;
      startH = histChartHeightPx;
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      document.addEventListener('touchmove', onTouchMove, { passive: false });
      document.addEventListener('touchend', onUp);
      document.body.style.cursor = 'ns-resize';
      document.body.style.userSelect = 'none';
    }
    handle.addEventListener('mousedown', function (e) {
      startDrag(e.clientY);
      e.preventDefault();
    });
    handle.addEventListener('touchstart', function (e) {
      if (!e.touches || !e.touches[0]) return;
      startDrag(e.touches[0].clientY);
      e.preventDefault();
    }, { passive: false });
  })();

  function histYAxisTitle(metric) {
    switch (metric) {
      case 'queue_wait': return 'Avg queue wait (s)';
      case 'processing': return 'Avg processing (s)';
      case 'duration': return 'Avg duration (s)';
      case 'error_rate': return 'Error %';
      default: return 'Requests';
    }
  }

  function setHistBanner(msg, isError) {
    var banner = document.getElementById('histBanner');
    if (!banner) return;
    if (!msg) {
      banner.classList.add('hidden');
      banner.textContent = '';
      return;
    }
    banner.classList.remove('hidden');
    banner.textContent = msg;
    banner.style.color = isError ? '#f88' : '#aaa';
  }

  function loadHistogram() {
    if (typeof Chart === 'undefined') return;
    applyHistChartHeight();
    var viewEl = document.getElementById('histView');
    var metricEl = document.getElementById('histMetric');
    if (!viewEl || !metricEl) return;
    var key = getKey();
    if (!key) {
      setHistBanner('Set admin key (Admin tab or ?key= in URL) to load histogram charts.', false);
      if (histChartModel) { histChartModel.destroy(); histChartModel = null; }
      if (histChartIp) { histChartIp.destroy(); histChartIp = null; }
      return;
    }
    setHistBanner('Loading…', false);
    var view = viewEl.value;
    var metric = metricEl.value;
    fetch(API_BASE + '/analytics/histogram?view=' + encodeURIComponent(view) + '&metric=' + encodeURIComponent(metric) + '&top_n=12', { headers: apiHeaders() })
      .then(function (r) {
        if (r.status === 403) return Promise.reject(new Error('Forbidden — invalid or missing admin key'));
        if (r.status === 503) {
          return r.json().then(function (j) {
            var d = (j && j.detail) ? j.detail : 'Histogram unavailable';
            return Promise.reject(new Error(d));
          }).catch(function () {
            return Promise.reject(new Error('Histogram unavailable (rollup tables missing or server error)'));
          });
        }
        if (!r.ok) return Promise.reject(new Error('HTTP ' + r.status));
        return r.json();
      })
      .then(function (data) {
        var bm = data.by_model || [];
        var bi = data.by_ip || [];
        if (bm.length === 0 && bi.length === 0) {
          setHistBanner('No rollup data in this time window yet. Series appear after requests complete and hourly/daily rollups are populated.', false);
        } else {
          setHistBanner('', false);
        }
        var labels = (data.buckets || []).map(function (b) {
          var s = String(b);
          return s.length > 16 ? s.slice(5, 16) : s;
        });
        function mkDs(series) {
          return (series || []).map(function (s, idx) {
            var lab = String(s.label || '');
            if (lab.length > 28) lab = lab.slice(0, 28) + '…';
            var hue = (idx * 47) % 360;
            return {
              label: lab,
              data: s.values || [],
              borderColor: 'hsl(' + hue + ',70%,55%)',
              backgroundColor: 'transparent',
              borderWidth: 1.5,
              fill: false,
              tension: 0.12,
              pointRadius: 0
            };
          });
        }
        var yTitle = histYAxisTitle(data.metric || metric);
        var opts = {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 }, color: '#bbb' } } },
          scales: {
            x: { ticks: { maxRotation: 60, minRotation: 30, color: '#888', font: { size: 9 } }, grid: { color: '#333' } },
            y: {
              beginAtZero: true,
              title: { display: true, text: yTitle, color: '#888' },
              ticks: { color: '#888' },
              grid: { color: '#333' }
            }
          }
        };
        var elM = document.getElementById('chartModel');
        var elI = document.getElementById('chartIp');
        if (histChartModel) { histChartModel.destroy(); histChartModel = null; }
        if (histChartIp) { histChartIp.destroy(); histChartIp = null; }
        histChartModel = new Chart(elM, { type: 'line', data: { labels: labels, datasets: mkDs(data.by_model) }, options: opts });
        histChartIp = new Chart(elI, { type: 'line', data: { labels: labels, datasets: mkDs(data.by_ip) }, options: opts });
      })
      .catch(function (e) {
        console.error(e);
        setHistBanner(String(e.message || e), true);
        if (histChartModel) { histChartModel.destroy(); histChartModel = null; }
        if (histChartIp) { histChartIp.destroy(); histChartIp = null; }
      });
  }

  var loadHistBtn = document.getElementById('loadHistogram');
  if (loadHistBtn) loadHistBtn.addEventListener('click', function () { loadHistogram(); });
  var histViewEl = document.getElementById('histView');
  var histMetricEl = document.getElementById('histMetric');
  if (histViewEl) histViewEl.addEventListener('change', function () { loadHistogram(); });
  if (histMetricEl) histMetricEl.addEventListener('change', function () { loadHistogram(); });

  /* Auto-load sessions on start if key is set */
  if (getKey()) { loadSessions(); }

  /* ================================================================
     ADMIN PANEL — button handlers
     ================================================================ */
  function showAdminResult(data) {
    var el = document.getElementById('adminResult');
    if (!el) return;
    el.style.display = 'block';
    el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  }
  function adminPost(url, body, onSuccess) {
    return fetch(url, { method: 'POST', headers: apiHeaders(), body: body !== undefined && body !== null ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) {
            throw new Error((data && data.detail) ? data.detail : ('HTTP ' + r.status));
          }
          return data;
        });
      })
      .then(function (data) { showAdminResult(data); if (typeof onSuccess === 'function') onSuccess(data); })
      .catch(function (e) { showAdminResult('Error: ' + (e.message || String(e))); });
  }
  var pauseBtn = document.getElementById('adminPause');
  if (pauseBtn) pauseBtn.addEventListener('click', function () { adminPost(API_BASE + '/testing', { pause: true }); });
  var resumeBtn = document.getElementById('adminResume');
  if (resumeBtn) resumeBtn.addEventListener('click', function () { adminPost(API_BASE + '/testing', { pause: false }); });
  var clearStaleBtn = document.getElementById('adminClearStale');
  if (clearStaleBtn) clearStaleBtn.addEventListener('click', function () { adminPost(API_BASE + '/clear-stale'); });
  var clearQueueBtn = document.getElementById('adminClearQueue');
  if (clearQueueBtn) clearQueueBtn.addEventListener('click', function () {
    openConfirmModal('Remove all jobs from the waiting queue? Active streams are not stopped.', function () {
      adminPost(API_BASE + '/clear-queue', undefined, function () { loadHome(); });
    });
  });
  var adminStopRequestBtn = document.getElementById('adminStopRequest');
  if (adminStopRequestBtn) adminStopRequestBtn.addEventListener('click', function () {
    var inp = document.getElementById('adminStopRequestId');
    var rid = inp && inp.value ? inp.value.trim() : '';
    if (!rid) { showAdminResult('Enter a request_id'); return; }
    adminPost(API_BASE + '/stop-request/' + encodeURIComponent(rid), undefined, function () { loadHome(); });
  });
  var dbDownBtn = document.getElementById('adminDbDown');
  if (dbDownBtn) dbDownBtn.addEventListener('click', function () { adminPost(API_BASE + '/testing', { db_available: false }); });
  var dbRestoreBtn = document.getElementById('adminDbRestore');
  if (dbRestoreBtn) dbRestoreBtn.addEventListener('click', function () { adminPost(API_BASE + '/testing', { db_available: true }); });
  var purgeBtn = document.getElementById('adminDbPurge');
  if (purgeBtn) purgeBtn.addEventListener('click', function () {
    var logs = document.getElementById('purgeRequestLogs') && document.getElementById('purgeRequestLogs').checked;
    var roll = document.getElementById('purgeAnalyticsRollups') && document.getElementById('purgeAnalyticsRollups').checked;
    if (!logs && !roll) { showAdminResult('Select at least one: request logs and/or analytics rollups'); return; }
    openConfirmModal('Permanently delete selected data? This cannot be undone.', function () {
      adminPost(API_BASE + '/admin/db/purge', { request_logs: !!logs, analytics_rollups: !!roll });
    });
  });
})();
