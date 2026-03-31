/* Orchestrator Dashboard — client-side JS */

// --- HTML escape utility ---

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// --- Chart rendering helpers ---

function renderCostChart(canvasId, costSeries) {
  const ctx = document.getElementById(canvasId);
  if (!ctx || !costSeries || costSeries.length === 0) return;

  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: costSeries.map(d => d.run_id.substring(0, 8)),
      datasets: [{
        label: 'Cost (USD)',
        data: costSeries.map(d => d.cost),
        backgroundColor: '#58a6ff',
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
        x: { ticks: { color: '#8b949e' }, grid: { display: false } },
      }
    }
  });
}

function renderModelChart(canvasId, modelUsage) {
  const ctx = document.getElementById(canvasId);
  if (!ctx || !modelUsage) return;

  const labels = Object.keys(modelUsage);
  const data = Object.values(modelUsage);
  const colors = ['#58a6ff', '#238636', '#d29922', '#da3633', '#8957e5'];

  new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: data,
        backgroundColor: colors.slice(0, labels.length),
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#c9d1d9', padding: 12 } }
      }
    }
  });
}

function renderPhaseChart(canvasId, phases) {
  const ctx = document.getElementById(canvasId);
  if (!ctx || !phases) return;

  const labels = Object.keys(phases);
  const costs = labels.map(k => phases[k].cost_usd || 0);
  const colors = labels.map(k => {
    const status = phases[k].status;
    if (status === 'completed') return '#238636';
    if (status === 'failed') return '#da3633';
    if (status === 'running') return '#d29922';
    return '#6e7681';
  });

  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Cost (USD)',
        data: costs,
        backgroundColor: colors,
        borderRadius: 4,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { beginAtZero: true, ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#c9d1d9' }, grid: { display: false } },
      }
    }
  });
}

// --- SSE live event streaming ---

function startSSE(runId, eventListId) {
  const container = document.getElementById(eventListId);
  if (!container) return;

  const evtSource = new EventSource(`/api/runs/${runId}/stream`);

  evtSource.onmessage = function(event) {
    const data = JSON.parse(event.data);

    if (data.event === 'stream_end') {
      evtSource.close();
      const dot = document.querySelector('.live-dot');
      if (dot) { dot.style.background = '#6e7681'; dot.style.animation = 'none'; }
      return;
    }

    const item = document.createElement('div');
    item.className = 'event-item';

    const time = data.ts ? new Date(data.ts).toLocaleTimeString() : '';
    const type = data.event || 'unknown';
    const details = Object.entries(data)
      .filter(([k]) => !['ts', 'run_id', 'event'].includes(k))
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(' ');

    item.innerHTML = `<span class="event-time">${escapeHtml(time)}</span><span class="event-type">${escapeHtml(type)}</span>${escapeHtml(details)}`;
    container.prepend(item);
  };

  evtSource.onerror = function() {
    evtSource.close();
    const dot = document.querySelector('.live-dot');
    if (dot) { dot.style.background = '#da3633'; dot.style.animation = 'none'; }
  };
}

// --- Auto-refresh ---

function autoRefresh(interval) {
  setInterval(() => {
    const active = document.querySelector('.badge-running');
    if (active) { location.reload(); }
  }, interval);
}

// --- Toast notification ---

function showToast(message, type) {
  const toast = document.getElementById('toast-notification');
  if (!toast) return;
  toast.textContent = message;
  toast.className = 'toast toast-' + type + ' toast-visible';
  setTimeout(function() {
    toast.className = 'toast';
  }, 3000);
}

// --- Advanced Options toggle ---

function initAdvancedOptions() {
  const toggle = document.getElementById('advanced-options-toggle');
  const section = document.getElementById('advanced-options-section');
  const icon = document.getElementById('advanced-toggle-icon');
  if (!toggle || !section) return;

  toggle.addEventListener('click', function() {
    const isHidden = section.style.display === 'none' || section.style.display === '';
    section.style.display = isHidden ? 'block' : 'none';
    section.setAttribute('aria-hidden', isHidden ? 'false' : 'true');
    toggle.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
    if (icon) icon.textContent = isHidden ? '▼' : '▶';
  });
}

// --- New Run form submission ---

function initNewRunForm() {
  const form = document.getElementById('new-run-form');
  if (!form) return;

  initAdvancedOptions();

  // Clear validation error when the user starts typing
  const featureRequestEl = form.querySelector('[name="feature_request"]');
  const featureRequestError = document.getElementById('feature-request-error');
  if (featureRequestEl && featureRequestError) {
    featureRequestEl.addEventListener('input', function() {
      featureRequestError.style.display = 'none';
    });
  }

  form.addEventListener('submit', async function(e) {
    e.preventDefault();

    // --- Client-side validation ---
    const featureVal = featureRequestEl ? featureRequestEl.value.trim() : '';
    if (!featureVal) {
      if (featureRequestError) {
        featureRequestError.style.display = 'block';
      }
      if (featureRequestEl) featureRequestEl.focus();
      return; // Prevent API call
    }
    if (featureRequestError) {
      featureRequestError.style.display = 'none';
    }

    const btn = document.getElementById('submit-btn');
    const status = document.getElementById('submit-status');
    btn.disabled = true;
    if (status) { status.textContent = 'Starting run...'; status.style.color = 'var(--text-dim)'; }

    function getVal(selector) {
      const el = form.querySelector(selector);
      return el ? el.value : '';
    }
    function getChecked(selector) {
      const el = form.querySelector(selector);
      return el ? el.checked : false;
    }

    const body = {
      feature_request: featureVal,
      workflow_type: getVal('[name="workflow_type"]') || 'full',
      model_routing: getVal('[name="model_routing"]') || 'default',
      config_path: getVal('[name="config_path"]') || null,
      custom_workflow: getVal('[name="custom_workflow"]') || null,
      max_budget_usd: parseFloat(getVal('[name="max_budget_usd"]')) || 50,
      max_concurrent_agents: parseInt(getVal('[name="max_concurrent_agents"]'), 10) || 0,
      debate: getChecked('[name="debate"]'),
      knowledge: getChecked('[name="knowledge"]'),
      dry_run: getChecked('[name="dry_run"]'),
    };

    try {
      const resp = await fetch('/api/v1/runs/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (resp.ok) {
        showToast('Run started successfully!', 'success');
        setTimeout(function() {
          window.location.href = '/runs/' + data.run_id + '/live';
        }, 600);
      } else {
        if (status) { status.textContent = 'Error: ' + (data.error || 'Unknown error'); status.style.color = 'var(--red)'; }
        btn.disabled = false;
      }
    } catch (err) {
      if (status) { status.textContent = 'Network error: ' + err.message; status.style.color = 'var(--red)'; }
      btn.disabled = false;
    }
  });
}

async function cancelRun(runId) {
  if (!confirm('Cancel run ' + runId.substring(0, 8) + '...? It will stop at the next safe point and can be resumed later.')) {
    return;
  }
  try {
    const resp = await fetch('/api/runs/' + runId + '/cancel', { method: 'POST' });
    const data = await resp.json();
    if (resp.ok) {
      location.reload();
    } else {
      alert('Failed to cancel: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Network error: ' + err.message);
  }
}

async function resumeRun(runId) {
  if (!confirm('Resume run ' + runId.substring(0, 8) + '...?')) {
    return;
  }
  try {
    const resp = await fetch('/api/runs/' + runId + '/resume', { method: 'POST' });
    const data = await resp.json();
    if (resp.ok) {
      window.location.href = data.redirect;
    } else {
      alert('Failed to resume: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Network error: ' + err.message);
  }
}

// --- Nav hamburger toggle (TASK-012) ---

function initNavHamburger() {
  const nav = document.getElementById('main-nav');
  const hamburger = document.getElementById('nav-hamburger');
  const drawer = document.getElementById('nav-drawer');
  if (!nav || !hamburger || !drawer) return;

  function openNav() {
    nav.classList.add('nav-open');
    hamburger.setAttribute('aria-expanded', 'true');
    hamburger.setAttribute('aria-label', 'Close navigation menu');
  }

  function closeNav() {
    nav.classList.remove('nav-open');
    hamburger.setAttribute('aria-expanded', 'false');
    hamburger.setAttribute('aria-label', 'Open navigation menu');
  }

  hamburger.addEventListener('click', function(e) {
    e.stopPropagation();
    if (nav.classList.contains('nav-open')) {
      closeNav();
    } else {
      openNav();
    }
  });

  // Close drawer when any link inside it is clicked (page navigation)
  drawer.querySelectorAll('a').forEach(function(link) {
    link.addEventListener('click', closeNav);
  });

  // Close drawer on Escape key; return focus to hamburger button
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && nav.classList.contains('nav-open')) {
      closeNav();
      hamburger.focus();
    }
  });

  // Close drawer when clicking outside the nav element
  document.addEventListener('click', function(e) {
    if (nav.classList.contains('nav-open') && !nav.contains(e.target)) {
      closeNav();
    }
  });
}

// --- Alert count badge (TASK-012) ---

async function fetchAlertBadge() {
  const badge = document.getElementById('nav-alert-badge');
  if (!badge) return;
  try {
    const resp = await fetch('/api/v1/alerts');
    if (!resp.ok) return;
    const data = await resp.json();
    const alerts = Array.isArray(data) ? data : (data.alerts || []);
    const count = alerts.length;
    if (count > 0) {
      badge.textContent = count > 99 ? '99+' : String(count);
      badge.removeAttribute('hidden');
      badge.setAttribute('aria-label', count + ' active alert' + (count === 1 ? '' : 's'));
    }
  } catch (_e) {
    // Network failure — badge silently absent
  }
}

// --- Critical alert banner (TASK-011) ---
// Fetches /api/v1/alerts, filters client-side for severity=critical + status=active,
// injects count into the banner, and shows it unless the user has dismissed it
// for this session (sessionStorage flag).

async function initCriticalAlertBanner() {
  var banner = document.getElementById('critical-alert-banner');
  if (!banner) return;

  // Respect per-session dismiss
  try {
    if (sessionStorage.getItem('critical-banner-dismissed') === '1') return;
  } catch (_e) {
    // sessionStorage unavailable (private mode restrictions) — show banner anyway
  }

  try {
    var resp = await fetch('/api/v1/alerts');
    if (!resp.ok) return;
    var data = await resp.json();
    var alerts = Array.isArray(data) ? data : (data.alerts || []);

    // Client-side filter: critical severity + active status
    var criticalActive = alerts.filter(function(a) {
      var sev = (a.severity || '').toLowerCase();
      var st  = (a.status  || 'active').toLowerCase();
      return sev === 'critical' && st === 'active';
    });

    if (criticalActive.length === 0) return;

    var count = criticalActive.length;
    var textEl = document.getElementById('critical-banner-text');
    if (textEl) {
      textEl.textContent = count + ' critical alert' + (count === 1 ? '' : 's') + ' active. ';
    }

    banner.removeAttribute('hidden');

    // Wire dismiss button
    var dismissBtn = document.getElementById('critical-banner-dismiss');
    if (dismissBtn) {
      dismissBtn.addEventListener('click', function() {
        try { sessionStorage.setItem('critical-banner-dismissed', '1'); } catch (_e) {}
        banner.setAttribute('hidden', '');
      });
    }
  } catch (_e) {
    // Network failure — banner silently absent
  }
}

// --- Alert row expand/collapse (TASK-011) ---
// Toggles the hidden detail row (with full JSON payload) when an alert row
// is clicked or activated via keyboard (Enter / Space).

function initAlertRowExpand() {
  var rows = document.querySelectorAll('.alert-row');
  if (!rows.length) return;

  rows.forEach(function(row) {
    row.addEventListener('click', function() {
      var idx = row.dataset.alertIndex;
      var detailRow = document.getElementById('alert-detail-' + idx);
      if (!detailRow) return;

      var isHidden = detailRow.hasAttribute('hidden');
      if (isHidden) {
        detailRow.removeAttribute('hidden');
        detailRow.removeAttribute('aria-hidden');
        row.setAttribute('aria-expanded', 'true');
      } else {
        detailRow.setAttribute('hidden', '');
        detailRow.setAttribute('aria-hidden', 'true');
        row.setAttribute('aria-expanded', 'false');
      }
    });

    // Keyboard activation (Enter / Space)
    row.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        row.click();
      }
    });
  });
}

document.addEventListener('DOMContentLoaded', function() {
  initNewRunForm();
  initNavHamburger();
  fetchAlertBadge();
  initCriticalAlertBanner();
});
