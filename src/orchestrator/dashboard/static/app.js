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
