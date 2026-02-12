(function () {
  'use strict';
  var DASHBOARD_BASE = window.location.pathname.replace(/\/?$/, '');
  var API_BASE = DASHBOARD_BASE.replace(/\/dashboard.*$/, '') || '/proxy';

  /* ---------- Auth helpers ---------- */
  function getKey() {
    var params = new URLSearchParams(window.location.search);
    var key = params.get('key') || sessionStorage.getItem('proxy_admin_key') || '';
    if (key) sessionStorage.setItem('proxy_admin_key', key);
    return key;
  }
  function apiHeaders() {
    var key = getKey();
    var h = { 'Content-Type': 'application/json' };
    if (key) h['X-Admin-Key'] = key;
    return h;
  }
  function setAuthStatus(ok, msg) {
    var el = document.getElementById('authStatus');
    el.textContent = msg || (ok ? 'Key set' : 'Set key for API');
    el.style.color = ok ? '#0a7' : '#888';
  }
  document.getElementById('adminKey').value = getKey();
  document.getElementById('setKey').addEventListener('click', function () {
    var key = document.getElementById('adminKey').value.trim();
    if (key) { sessionStorage.setItem('proxy_admin_key', key); setAuthStatus(true, 'Key set'); }
  });
  setAuthStatus(!!getKey());

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

  /* ---------- Tabs ---------- */
  document.querySelectorAll('.tabs button').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tab = btn.getAttribute('data-tab');
      document.querySelectorAll('.tabs button').forEach(function (b) { b.classList.remove('active'); });
      document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.add('hidden'); });
      btn.classList.add('active');
      var panel = document.getElementById(tab);
      if (panel) panel.classList.remove('hidden');
    });
  });
  document.querySelector('.tabs button[data-tab="home"]').classList.add('active');
  document.getElementById('conversations').classList.add('hidden');
  document.getElementById('history').classList.add('hidden');

  /* ================================================================
     HOME (dashboard overview)
     ================================================================ */
  var HOME_DISPLAY_LIMIT = 10;
  var HOME_RECENT_LIMIT = 5;
  var DEBOUNCE_MS = 500;
  var homeDebounceTimer = null;
  var sessionsDebounceTimer = null;
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
      return '<tr><td class="dash-icon">' + icon + '</td><td>' + escapeHtml(String(r.model || '?').slice(0, 40)) + '</td><td class="dash-dim">' + escapeHtml(String(r.ip || '?').slice(0, 15)) + '</td><td class="dash-num">' + durStr + '</td></tr>';
    }).join('');
    el.innerHTML = '<table class="dash-table"><thead><tr><th>St</th><th>Model</th><th>IP</th><th>Time</th></tr></thead><tbody>' + rows + '</tbody></table>';
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
      ? fetch(API_BASE + '/query_db?limit=' + HOME_RECENT_LIMIT + '&status=completed,error&sort_by=timestamp_completed&sort_order=desc&from_time=' + encodeURIComponent(fromTime), { headers: apiHeaders() }).then(function (r) { if (r.status === 403) throw new Error('Forbidden'); return r.json(); }).catch(function (e) { return { error: String(e.message || e) }; })
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
  var liveMode = false; // true when user clicked "Go live" (chunk streaming into threads)
  var liveAccumulated = {};
  var liveThinkingAccumulated = {};
  var reconnectDelay = 2000;
  var maxReconnectDelay = 30000;
  var reconnectTimer = null;
  var intentionalDisconnect = false;

  function setWsStatus(text, color) {
    if (wsStatusEl) { wsStatusEl.textContent = text; wsStatusEl.style.color = color || ''; }
    if (homeWsStatusEl) { homeWsStatusEl.textContent = text; homeWsStatusEl.style.color = color || ''; }
  }

  function buildLiveWsUrl() {
    var key = getKey();
    if (!key) return null;
    var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return proto + '//' + window.location.host + API_BASE + '/live?key=' + encodeURIComponent(key);
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
    };
    ws.onmessage = function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        if (msg.type === 'request_queued' || msg.type === 'request_processing' || msg.type === 'request_started' || msg.type === 'request_completed') {
          debouncedLoadHome();
        }
        if (msg.type === 'request_started') {
          var sid = msg.metadata && msg.metadata.session_id;
          if (sid && !currentSessionRequests) pendingLiveOpen = sid;
          debouncedLoadSessions();
        } else if (msg.type === 'request_completed') {
          delete liveAccumulated[msg.request_id];
          delete liveThinkingAccumulated[msg.request_id];
          debouncedLoadSessions();
        } else if (msg.type === 'chunk' && liveMode) {
          var kind = msg.kind || 'content';
          var fullText = msg.full !== undefined ? msg.full : ((liveAccumulated[msg.request_id] || '') + (msg.delta || ''));
          var fullThinking = msg.full_thinking !== undefined ? msg.full_thinking : ((liveThinkingAccumulated[msg.request_id] || '') + (kind === 'thinking' ? (msg.delta || '') : ''));
          liveAccumulated[msg.request_id] = fullText;
          liveThinkingAccumulated[msg.request_id] = fullThinking;
          var row = findAssistantDiv(msg.request_id);
          if (row) {
            if (kind === 'thinking') {
              var thinkingPre = row.querySelector('.thread-thinking .streamable-thinking');
              if (thinkingPre) thinkingPre.textContent = fullThinking;
              var thinkingDetails = row.querySelector('.thread-thinking');
              if (thinkingDetails) thinkingDetails.setAttribute('open', 'open');
            } else {
              var streamEl = row.querySelector('.body.streamable');
              if (streamEl) streamEl.textContent = fullText;
              var thinkingDetails = row.querySelector('.thread-thinking');
              if (thinkingDetails) thinkingDetails.removeAttribute('open');
            }
            if (currentSessionRequests && currentSessionRequests.some(function (r) { return r.request_id === msg.request_id; })) scrollThreadToBottom();
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

  var convLimitEl = document.getElementById('convLimit');
  if (convLimitEl) {
    var savedConvLimit = sessionStorage.getItem('conv_limit');
    if (savedConvLimit !== null) convLimitEl.value = savedConvLimit;
    convLimitEl.addEventListener('change', function () { sessionStorage.setItem('conv_limit', this.value); });
  }
  var historyLimitEl = document.getElementById('limit');
  if (historyLimitEl) {
    var savedHistoryLimit = sessionStorage.getItem('history_limit');
    if (savedHistoryLimit !== null) historyLimitEl.value = savedHistoryLimit;
    historyLimitEl.addEventListener('change', function () { sessionStorage.setItem('history_limit', this.value); });
  }

  function findAssistantDiv(requestId) {
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
  var currentSessionRequests = null;
  var pendingLiveOpen = null; // session_id string to auto-open after loadSessions

  function loadSessions() {
    var key = getKey();
    if (!key) { setAuthStatus(false, 'Set key first'); return; }
    var limitEl = document.getElementById('convLimit');
    var limit = (limitEl && limitEl.value) ? parseInt(limitEl.value, 10) : 100;
    if (isNaN(limit) || limit < 1) limit = 100;
    if (limit > 1000) limit = 1000;
    fetch(API_BASE + '/query_db?limit=' + limit + '&sort_by=timestamp_received&sort_order=desc', { headers: apiHeaders() })
      .then(function (r) { if (r.status === 403) throw new Error('Forbidden'); return r.json(); })
      .then(function (data) {
        var bySession = {};
        (data.requests || []).forEach(function (req) {
          var sid = req.session_id || 'no-session';
          if (!bySession[sid]) bySession[sid] = [];
          bySession[sid].push(req);
        });
        // Sort turns within each session chronologically
        Object.keys(bySession).forEach(function (sid) {
          bySession[sid].sort(function (a, b) { return (a.timestamp_received || '').localeCompare(b.timestamp_received || ''); });
        });

        // Filter out sessions where ALL requests have empty/N/A prompts (warmup/loading)
        Object.keys(bySession).forEach(function (sid) {
          var allEmpty = bySession[sid].every(function (r) { return isEmptyPrompt(r.prompt_text); });
          if (allEmpty) delete bySession[sid];
        });

        // Build session list
        var listEl = document.getElementById('sessionList');
        listEl.innerHTML = '';
        var sessionIds = Object.keys(bySession).sort(function (a, b) {
          var ra = bySession[a], rb = bySession[b];
          var ta = ra.length && ra[ra.length - 1].timestamp_received ? ra[ra.length - 1].timestamp_received : '';
          var tb = rb.length && rb[rb.length - 1].timestamp_received ? rb[rb.length - 1].timestamp_received : '';
          return tb.localeCompare(ta);
        });
        sessionIds.forEach(function (sid) {
          var reqs = bySession[sid];
          var first = reqs[0];
          var last = reqs[reqs.length - 1];
          var time = last.timestamp_received ? new Date(last.timestamp_received).toLocaleString() : '';
          var preview = (first.prompt_text || '').slice(0, 80);
          if (first.prompt_text && first.prompt_text.length > 80) preview += '…';
          var inProgress = reqs.some(function (r) { return r.status === 'processing' || r.status === 'queued'; });
          var item = document.createElement('div');
          item.className = 'session-item' + (inProgress ? ' session-live' : '');
          item.innerHTML =
            '<div class="session-header"><strong>' + escapeHtml(first.model || '') + '</strong> · ' +
            reqs.length + ' turn(s) · ' + escapeHtml(time) +
            (inProgress ? ' <span class="live-badge">live</span>' : '') +
            '</div><div class="session-preview">' + escapeHtml(preview) + '</div>';
          item.addEventListener('click', function () { showSessionThread(sid, reqs); });
          listEl.appendChild(item);
        });

        // If we're viewing a thread, refresh it
        if (currentSessionRequests && bySession[currentSessionRequests._sid]) {
          showSessionThread(currentSessionRequests._sid, bySession[currentSessionRequests._sid]);
        }

        // Auto-open a session that just went live
        if (pendingLiveOpen && bySession[pendingLiveOpen]) {
          var sid = pendingLiveOpen;
          pendingLiveOpen = null;
          document.getElementById('sessionList').style.display = 'none';
          document.getElementById('sessionThread').classList.remove('hidden');
          showSessionThread(sid, bySession[sid]);
        } else {
          pendingLiveOpen = null;
        }
      })
      .catch(function (e) { console.error('Load sessions failed:', e); });
  }
  document.getElementById('loadSessions').addEventListener('click', loadSessions);
  document.getElementById('backToSessions').addEventListener('click', function () {
    document.getElementById('sessionThread').classList.add('hidden');
    document.getElementById('sessionList').style.display = '';
    currentSessionRequests = null;
  });

  /* -- Thread view -- */
  function showSessionThread(sid, requests) {
    currentSessionRequests = requests;
    currentSessionRequests._sid = sid;
    document.getElementById('sessionList').style.display = 'none';
    document.getElementById('sessionThread').classList.remove('hidden');
    var titleModel = requests.length ? (requests[0].model || '') : '';
    document.getElementById('sessionTitle').textContent = titleModel + ' — ' + requests.length + ' turn(s)';
    var container = document.getElementById('threadMessages');
    container.innerHTML = '';
    requests.forEach(function (req) {
      var reqId = req.request_id || '';
      var userTime = req.timestamp_received ? new Date(req.timestamp_received).toLocaleString() : '';
      var asstDuration = fmtDurationShort(req.duration_seconds);
      var isLive = (req.status === 'processing' || req.status === 'queued');
      // Determine assistant body: use live accumulated text if available, else API response_text or error_message
      var responseBody = '';
      if (liveAccumulated[reqId]) {
        responseBody = escapeHtml(liveAccumulated[reqId]);
      } else {
        var respText = req.response_text || '';
        if (!respText && req.error_message) respText = req.error_message;
        responseBody = (respText && (respText.indexOf('[HTTP') === 0 || respText.indexOf('[Error]') === 0))
          ? escapeHtml(respText)
          : renderMarkdown(respText);
      }
      var isError = req.status === 'error';
      // User message
      var userDiv = document.createElement('div');
      userDiv.className = 'thread-msg user';
      userDiv.innerHTML =
        '<div class="role">User · ' + escapeHtml(req.ip_address || '') + ' · ' + escapeHtml(userTime) +
        ' <button class="meta-toggle" title="Show metadata">&#9432;</button></div>' +
        '<div class="body">' + escapeHtml(req.prompt_text || '') + '</div>' +
        '<div class="thread-meta hidden"></div>';
      userDiv.querySelector('.meta-toggle').addEventListener('click', function (e) {
        e.stopPropagation();
        var metaDiv = userDiv.querySelector('.thread-meta');
        if (metaDiv.classList.contains('hidden')) {
          metaDiv.innerHTML = buildInlineMeta(req);
          metaDiv.classList.remove('hidden');
        } else {
          metaDiv.classList.add('hidden');
        }
      });
      container.appendChild(userDiv);
      // Assistant message: optional thinking block (collapsible; for past collapsed by default; for live open when thinking is streaming)
      var hasThinking = !!(req.thinking_text && req.thinking_text.trim());
      var liveThinking = isLive && (liveThinkingAccumulated[reqId] || '');
      var thinkingHtml = '';
      if (hasThinking || liveThinking) {
        var thinkingContent = (req.thinking_text && req.thinking_text.trim()) || liveThinking || '';
        var thinkingEscaped = escapeHtml(thinkingContent);
        var thinkingOpen = isLive && liveThinking && !(liveAccumulated[reqId] || responseBody);
        thinkingHtml = '<details class="thread-thinking' + (isLive ? ' thread-thinking-live' : '') + '"' + (thinkingOpen ? ' open' : '') + '><summary>Thinking</summary><pre class="thread-thinking-body' + (isLive ? ' streamable-thinking' : '') + '">' + thinkingEscaped + '</pre></details>';
      }
      var asstDiv = document.createElement('div');
      asstDiv.className = 'thread-msg' + (isLive ? ' thread-msg-live' : '') + (isError ? ' thread-msg-error' : '');
      asstDiv.setAttribute('data-request-id', reqId);
      asstDiv.innerHTML =
        '<div class="role">Assistant · ' + escapeHtml(req.model || '') + ' · ' +
        (isLive ? '<span class="streaming-indicator">streaming…</span>' : escapeHtml(asstDuration)) +
        '</div>' +
        thinkingHtml +
        '<div class="body ' + (isLive ? 'streamable' : 'markdown-body') + '">' + responseBody + '</div>';
      container.appendChild(asstDiv);
    });
    requestAnimationFrame(function () { scrollThreadToBottom(); });
  }

  function buildInlineMeta(req) {
    var rows = [
      ['Request ID', req.request_id],
      ['IP', req.ip_address],
      ['Model', req.model],
      ['Status', req.status],
      ['Duration', fmtDuration(req.duration_seconds)],
      ['Queue wait', fmtDuration(req.queue_wait_seconds)],
      ['Processing', fmtDuration(req.processing_time_seconds)],
      ['Priority', req.priority_score],
      ['Session', req.session_id],
      ['Endpoint', req.endpoint],
      ['User-Agent', req.user_agent],
      ['Received', req.timestamp_received],
      ['Started', req.timestamp_started],
      ['Completed', req.timestamp_completed],
      ['Error', req.error_message]
    ].filter(function (r) { return r[1] != null && r[1] !== ''; });
    var html = '<table class="inline-meta-table"><tbody>';
    rows.forEach(function (r) { html += '<tr><td class="meta-key">' + escapeHtml(String(r[0])) + '</td><td>' + escapeHtml(String(r[1])) + '</td></tr>'; });
    html += '</tbody></table>';
    return html;
  }

  /* ================================================================
     HISTORY
     ================================================================ */
  function loadHistory() {
    var key = getKey();
    if (!key) { setAuthStatus(false, 'Set key first'); return; }
    var limit = document.getElementById('limit').value;
    var status = document.getElementById('filterStatus').value.trim();
    var model = document.getElementById('filterModel').value.trim();
    var ip = document.getElementById('filterIp').value.trim();
    var url = API_BASE + '/query_db?limit=' + encodeURIComponent(limit);
    if (status) url += '&status=' + encodeURIComponent(status);
    if (model) url += '&model=' + encodeURIComponent(model);
    if (ip) url += '&ip_address=' + encodeURIComponent(ip);
    fetch(url, { headers: apiHeaders() })
      .then(function (r) { if (r.status === 403) throw new Error('Forbidden'); return r.json(); })
      .then(function (data) {
        var tbody = document.querySelector('#historyTable tbody');
        tbody.innerHTML = '';
        (data.requests || []).forEach(function (req) {
          var tr = document.createElement('tr');
          var time = req.timestamp_received ? new Date(req.timestamp_received).toLocaleString() : '';
          tr.innerHTML =
            '<td><code>' + escapeHtml((req.request_id || '').slice(0, 12)) + '…</code></td>' +
            '<td>' + escapeHtml(time) + '</td>' +
            '<td>' + escapeHtml(req.model || '') + '</td>' +
            '<td>' + escapeHtml(req.ip_address || '') + '</td>' +
            '<td>' + escapeHtml(req.status || '') + '</td>' +
            '<td>' + fmtDuration(req.duration_seconds) + '</td>' +
            '<td>' + fmtDuration(req.queue_wait_seconds) + '</td>' +
            '<td>' + fmtDuration(req.processing_time_seconds) + '</td>' +
            '<td>' + escapeHtml((req.session_id || '').slice(0, 16)) + '</td>' +
            '<td>' + escapeHtml((req.endpoint || '').replace(/^\/+/, '')) + '</td>' +
            '<td><a href="#" data-rid="' + escapeHtml(req.request_id) + '">Detail</a></td>';
          tr.querySelector('a').addEventListener('click', function (e) { e.preventDefault(); openDetail(req.request_id); });
          tbody.appendChild(tr);
        });
      })
      .catch(function (e) { alert('Load failed: ' + e.message); });
  }
  document.getElementById('loadHistory').addEventListener('click', loadHistory);

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
          ['Status', req.status], ['Duration (s)', req.duration_seconds],
          ['Queue wait (s)', req.queue_wait_seconds], ['Processing (s)', req.processing_time_seconds],
          ['Priority score', req.priority_score], ['Session ID', req.session_id],
          ['Endpoint', req.endpoint], ['User-Agent', req.user_agent],
          ['Received', req.timestamp_received], ['Started', req.timestamp_started],
          ['Completed', req.timestamp_completed], ['Error', req.error_message]
        ].filter(function (r) { return r[1] != null && r[1] !== ''; });
        var metaHtml = '<table class="detail-meta-table"><tbody>';
        metaRows.forEach(function (r) { metaHtml += '<tr><td class="meta-key">' + escapeHtml(String(r[0])) + '</td><td>' + escapeHtml(String(r[1])) + '</td></tr>'; });
        metaHtml += '</tbody></table>';
        document.querySelector('.detail-meta').innerHTML = metaHtml;
        var detailParts = '--- Request (prompt) ---\n' + (req.prompt_text || '');
        if (req.thinking_text && req.thinking_text.trim()) {
          detailParts += '\n\n--- Thinking ---\n' + req.thinking_text.trim() + '\n\n--- Response ---\n' + (req.response_text || '');
        } else {
          detailParts += '\n\n--- Response ---\n' + (req.response_text || '');
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

  /* Auto-load sessions on start if key is set */
  if (getKey()) { loadSessions(); }
})();
