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
  document.querySelector('.tabs button[data-tab="conversations"]').classList.add('active');
  document.getElementById('history').classList.add('hidden');

  /* ================================================================
     CONVERSATIONS (with embedded live + polling)
     ================================================================ */
  var ws = null;
  var wsStatusEl = document.getElementById('wsStatus');
  var pollTimer = null;
  var liveAccumulated = {}; // request_id -> accumulated full text from WS chunks

  /* -- WebSocket live -- */
  document.getElementById('connectWs').addEventListener('click', function () {
    var key = getKey();
    if (!key) { setAuthStatus(false, 'Set key first'); return; }
    if (ws && ws.readyState === WebSocket.OPEN) return;
    var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + window.location.host + API_BASE + '/live?key=' + encodeURIComponent(key);
    ws = new WebSocket(url);
    wsStatusEl.textContent = 'connecting…';
    ws.onopen = function () {
      wsStatusEl.textContent = 'live';
      wsStatusEl.style.color = '#0f0';
      document.getElementById('connectWs').classList.add('hidden');
      document.getElementById('disconnectWs').classList.remove('hidden');
    };
    ws.onmessage = function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        if (msg.type === 'request_started') {
          var sid = msg.metadata && msg.metadata.session_id;
          // Refresh session list to show the new request + auto-open if live
          if (sid && !currentSessionRequests) {
            pendingLiveOpen = sid;
          }
          loadSessions();
        } else if (msg.type === 'chunk') {
          // Store accumulated text so thread rebuilds preserve streaming content
          var fullText = msg.full !== undefined ? msg.full : ((liveAccumulated[msg.request_id] || '') + (msg.delta || ''));
          liveAccumulated[msg.request_id] = fullText;
          // Update DOM directly if the assistant div exists
          var row = findAssistantDiv(msg.request_id);
          if (row) {
            var streamEl = row.querySelector('.body.streamable');
            if (streamEl) { streamEl.textContent = fullText; }
          }
        } else if (msg.type === 'request_completed') {
          delete liveAccumulated[msg.request_id];
          loadSessions();
        }
      } catch (_) {}
    };
    ws.onclose = function () {
      wsStatusEl.textContent = '';
      document.getElementById('connectWs').classList.remove('hidden');
      document.getElementById('disconnectWs').classList.add('hidden');
      ws = null;
    };
    ws.onerror = function () { wsStatusEl.textContent = 'error'; wsStatusEl.style.color = '#f44'; };
  });
  document.getElementById('disconnectWs').addEventListener('click', function () {
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
  });

  function findAssistantDiv(requestId) {
    var divs = document.querySelectorAll('#threadMessages .thread-msg[data-request-id]');
    for (var i = 0; i < divs.length; i++) {
      if (divs[i].getAttribute('data-request-id') === requestId) return divs[i];
    }
    return null;
  }

  /* -- Polling -- */
  document.getElementById('autoPoll').addEventListener('change', function () {
    if (this.checked) {
      loadSessions();
      pollTimer = setInterval(loadSessions, 10000);
    } else {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }
  });

  /* -- Sessions list -- */
  var currentSessionRequests = null;
  var pendingLiveOpen = null; // session_id string to auto-open after loadSessions

  function loadSessions() {
    var key = getKey();
    if (!key) { setAuthStatus(false, 'Set key first'); return; }
    fetch(API_BASE + '/query_db?limit=500&sort_by=timestamp_received&sort_order=desc', { headers: apiHeaders() })
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
      // Determine assistant body: use live accumulated text if available, else API response_text
      var responseBody = '';
      if (liveAccumulated[reqId]) {
        responseBody = escapeHtml(liveAccumulated[reqId]);
      } else {
        responseBody = renderMarkdown(req.response_text || '');
      }
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
      // Assistant message
      var asstDiv = document.createElement('div');
      asstDiv.className = 'thread-msg' + (isLive ? ' thread-msg-live' : '');
      asstDiv.setAttribute('data-request-id', reqId);
      asstDiv.innerHTML =
        '<div class="role">Assistant · ' + escapeHtml(req.model || '') + ' · ' +
        (isLive ? '<span class="streaming-indicator">streaming…</span>' : escapeHtml(asstDuration)) +
        '</div>' +
        '<div class="body ' + (isLive ? 'streamable' : 'markdown-body') + '">' + responseBody + '</div>';
      container.appendChild(asstDiv);
    });
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
    var url = API_BASE + '/query_db?limit=' + encodeURIComponent(limit);
    if (status) url += '&status=' + encodeURIComponent(status);
    if (model) url += '&model=' + encodeURIComponent(model);
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
          ['Received', req.timestamp_received], ['Started', req.timestamp_started],
          ['Completed', req.timestamp_completed], ['Error', req.error_message]
        ].filter(function (r) { return r[1] != null && r[1] !== ''; });
        var metaHtml = '<table class="detail-meta-table"><tbody>';
        metaRows.forEach(function (r) { metaHtml += '<tr><td class="meta-key">' + escapeHtml(String(r[0])) + '</td><td>' + escapeHtml(String(r[1])) + '</td></tr>'; });
        metaHtml += '</tbody></table>';
        document.querySelector('.detail-meta').innerHTML = metaHtml;
        document.getElementById('detailText').textContent =
          '--- Request (prompt) ---\n' + (req.prompt_text || '') + '\n\n--- Response ---\n' + (req.response_text || '');
        document.getElementById('detailRaw').textContent = JSON.stringify(req, null, 2);
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
