/* Orchestrator Dashboard — real-time KPI updater via SSE */

(function () {
  'use strict';

  // -----------------------------------------------------------------------
  // DOM element IDs that map to overview JSON keys
  // -----------------------------------------------------------------------
  var KPI_ELEMENTS = {
    'kpi-active-runs':  function (data) { return data.active_runs != null ? String(data.active_runs) : null; },
    'kpi-runs-today':   function (data) { return data.runs_today  != null ? String(data.runs_today)  : null; },
    'kpi-cost-today':   function (data) {
      if (data.cost_today == null) return null;
      return '$' + Number(data.cost_today).toFixed(4);
    },
    'kpi-burn-rate':    function (data) {
      if (data.burn_rate == null) return null;
      return '$' + Number(data.burn_rate).toFixed(4) + '/hr';
    },
    'kpi-active-alerts': function (data) { return data.active_alerts != null ? String(data.active_alerts) : null; },
  };

  // -----------------------------------------------------------------------
  // Update KPI card text content from a parsed overview payload
  // -----------------------------------------------------------------------
  function updateKpis(data) {
    Object.keys(KPI_ELEMENTS).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var value = KPI_ELEMENTS[id](data);
      if (value !== null) {
        el.textContent = value;
      }
    });

    // --- Alert count colouring ---
    var alertEl = document.getElementById('kpi-active-alerts');
    if (alertEl && data.active_alerts != null) {
      alertEl.style.color = data.active_alerts > 0 ? 'var(--red)' : '';
    }

    // --- SLO compliance badge ---
    var sloBadge = document.getElementById('slo-summary-status');
    if (sloBadge && data.slo_summary != null) {
      var allPassing = data.slo_summary.all_passing;
      sloBadge.textContent = allPassing ? 'All SLOs passing' : 'SLO violation detected';
      sloBadge.className = 'badge ' + (allPassing ? 'badge-completed' : 'badge-failed');
    }
  }

  // -----------------------------------------------------------------------
  // Exponential back-off reconnect (1s → 2s → 4s … capped at 30s)
  // -----------------------------------------------------------------------
  var _backoffMs = 1000;
  var _maxBackoffMs = 30000;
  var _es = null;

  function connect() {
    if (_es) {
      _es.close();
      _es = null;
    }

    try {
      _es = new EventSource('/api/v1/dashboard/sse');
    } catch (e) {
      // EventSource not supported or connection refused — retry later.
      scheduleReconnect();
      return;
    }

    _es.onopen = function () {
      // Reset backoff on successful connection.
      _backoffMs = 1000;
    };

    _es.onmessage = function (evt) {
      if (!evt.data) return;
      try {
        var payload = JSON.parse(evt.data);
        // Ignore internal control messages.
        if (payload.event === 'stream_timeout' || payload.error) {
          _es.close();
          scheduleReconnect();
          return;
        }
        updateKpis(payload);
      } catch (e) {
        // Malformed JSON — ignore silently.
      }
    };

    _es.onerror = function () {
      _es.close();
      _es = null;
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    var delay = _backoffMs;
    _backoffMs = Math.min(_backoffMs * 2, _maxBackoffMs);
    setTimeout(connect, delay);
  }

  // -----------------------------------------------------------------------
  // Initialise on DOM ready
  // -----------------------------------------------------------------------
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }
})();
