/**
 * artifacts.js — Artifact browser slide-over panel, version history,
 * content viewer with syntax highlighting, copy/download, and search filter.
 *
 * TASK-007: Rich artifact browsing experience for /runs/{run_id}/artifacts-view
 */
(function () {
  'use strict';

  // ─── State ───────────────────────────────────────────────────────────────

  var _currentRunId = null;
  var _currentName = null;
  var _currentVersion = null;
  var _currentContent = null; // raw JSON string for copy/download

  // ─── HTML escape utility ────────────────────────────────────────────────

  function _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ─── Slide-over open / close ────────────────────────────────────────────

  window.openSlideover = function (runId, name) {
    _currentRunId = runId;
    _currentName = name;
    _currentVersion = null;
    _currentContent = null;

    // Reset inner content
    var title = document.getElementById('slideover-title');
    var versionsEl = document.getElementById('slideover-versions');
    var contentSection = document.getElementById('slideover-content-section');

    if (title) title.textContent = 'Version History — ' + name;
    if (versionsEl) versionsEl.innerHTML = '<p style="color:var(--text-dim);font-size:0.85rem;">Loading…</p>';
    if (contentSection) contentSection.style.display = 'none';

    // Show the panel
    var panel = document.getElementById('version-slideover');
    var overlay = document.getElementById('slideover-overlay');
    if (panel) panel.style.transform = 'translateX(0)';
    if (overlay) overlay.style.display = 'block';

    _loadVersionHistory(runId, name);
  };

  window.closeSlideover = function () {
    var panel = document.getElementById('version-slideover');
    var overlay = document.getElementById('slideover-overlay');
    if (panel) panel.style.transform = 'translateX(100%)';
    if (overlay) overlay.style.display = 'none';
  };

  // ─── Load version history ────────────────────────────────────────────────

  function _loadVersionHistory(runId, name) {
    var versionsEl = document.getElementById('slideover-versions');
    if (!versionsEl) return;

    fetch('/api/v1/runs/' + encodeURIComponent(runId) + '/artifacts/' + encodeURIComponent(name) + '/versions')
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (versions) {
        if (!versions.length) {
          versionsEl.innerHTML = '<p style="color:var(--text-dim);font-size:0.85rem;">No version history found.</p>';
          return;
        }

        var html = '<div style="font-size:0.8rem;color:var(--text-dim);margin-bottom:10px;">'
          + versions.length + ' version' + (versions.length !== 1 ? 's' : '') + '</div>';

        html += '<ul style="list-style:none;margin:0;padding:0;">';
        versions.forEach(function (v) {
          var statusColor = v.run_status === 'completed' ? '#3fb950'
            : v.run_status === 'failed' ? '#f85149' : '#8b949e';
          var sizeStr = _humanSize(v.size_bytes || 0);
          var ts = v.created_at ? v.created_at.substring(0, 19) : '—';

          html += '<li style="'
            + 'padding:10px 12px;border-radius:6px;cursor:pointer;'
            + 'border:1px solid var(--border);margin-bottom:8px;'
            + 'transition:background 0.15s;"'
            + ' onmouseover="this.style.background=\'var(--bg-hover)\'"'
            + ' onmouseout="this.style.background=\'\'"'
            + ' onclick="loadVersionContent(\''
            + _esc(encodeURIComponent(runId)) + '\',\''
            + _esc(encodeURIComponent(name)) + '\',' + v.version + ')">'
            + '<div style="display:flex;justify-content:space-between;align-items:center;">'
            + '<strong style="font-size:0.875rem;">v' + _esc(String(v.version)) + '</strong>'
            + '<span style="color:' + statusColor + ';font-size:0.75rem;font-weight:600;">' + _esc(v.run_status || 'unknown') + '</span>'
            + '</div>'
            + '<div style="margin-top:4px;font-size:0.75rem;color:var(--text-dim);display:flex;gap:16px;flex-wrap:wrap;">'
            + '<span>' + _esc(v.agent || '—') + '</span>'
            + '<span>' + _esc(sizeStr) + '</span>'
            + '<span>' + _esc(ts) + '</span>'
            + '</div>'
            + '</li>';
        });
        html += '</ul>';
        versionsEl.innerHTML = html;
      })
      .catch(function (err) {
        versionsEl.innerHTML = '<p style="color:#f85149;font-size:0.85rem;">Error loading history: ' + _esc(err.message) + '</p>';
      });
  }

  // ─── Load version content ────────────────────────────────────────────────

  window.loadVersionContent = function (encodedRunId, encodedName, version) {
    var runId = decodeURIComponent(encodedRunId);
    var name = decodeURIComponent(encodedName);
    _currentVersion = version;

    var contentSection = document.getElementById('slideover-content-section');
    var contentTitle = document.getElementById('slideover-content-title');
    var viewer = document.getElementById('slideover-content-viewer');
    var copyBtn = document.getElementById('btn-copy');
    var dlBtn = document.getElementById('btn-download');

    if (!contentSection || !viewer) return;

    contentSection.style.display = 'block';
    if (contentTitle) contentTitle.textContent = name + ' — v' + version;
    viewer.innerHTML = '<span style="color:var(--text-dim)">Loading…</span>';
    if (copyBtn) { copyBtn.textContent = 'Copy'; copyBtn.disabled = false; }
    if (dlBtn) { dlBtn.disabled = false; }

    var url = '/api/v1/runs/' + encodeURIComponent(runId)
      + '/artifacts/' + encodeURIComponent(name)
      + '/versions/' + version;

    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        _currentContent = JSON.stringify(data, null, 2);
        viewer.innerHTML = _syntaxHighlightJson(data);
      })
      .catch(function (err) {
        _currentContent = null;
        viewer.innerHTML = '<span style="color:#f85149;">Error: ' + _esc(err.message) + '</span>';
      });
  };

  // ─── Syntax highlighting ─────────────────────────────────────────────────

  /**
   * Return HTML string with <span class="json-*"> syntax highlighting.
   * Safely escapes HTML before wrapping tokens.
   */
  function _syntaxHighlightJson(obj) {
    var json = JSON.stringify(obj, null, 2);

    // Token regex: strings (with optional colon for keys), booleans, null, numbers, punctuation
    var tokenRe = /(\"(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\\"])*\"(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?|[{}\[\],])/g;

    var result = '';
    var lastIndex = 0;
    var match;

    while ((match = tokenRe.exec(json)) !== null) {
      // Append non-token gap (whitespace / newlines) — just escape it
      if (match.index > lastIndex) {
        result += _esc(json.slice(lastIndex, match.index));
      }

      var token = match[1];
      var cls;

      if (/^"/.test(token)) {
        cls = /:$/.test(token) ? 'json-key' : 'json-string';
      } else if (token === 'true' || token === 'false') {
        cls = 'json-bool';
      } else if (token === 'null') {
        cls = 'json-null';
      } else if (/^[{}\[\],]$/.test(token)) {
        cls = 'json-punct';
      } else {
        cls = 'json-number';
      }

      result += '<span class="' + cls + '">' + _esc(token) + '</span>';
      lastIndex = match.index + match[0].length;
    }

    // Append trailing non-token text
    if (lastIndex < json.length) {
      result += _esc(json.slice(lastIndex));
    }

    return result;
  }

  // ─── Copy to clipboard ───────────────────────────────────────────────────

  window.copyArtifactContent = function () {
    if (!_currentContent) return;
    var btn = document.getElementById('btn-copy');
    navigator.clipboard.writeText(_currentContent).then(function () {
      if (btn) {
        btn.textContent = '✓ Copied';
        setTimeout(function () { btn.textContent = 'Copy'; }, 2000);
      }
    }).catch(function () {
      // Fallback: textarea-based copy for older browsers
      var ta = document.createElement('textarea');
      ta.value = _currentContent;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      if (btn) {
        btn.textContent = '✓ Copied';
        setTimeout(function () { btn.textContent = 'Copy'; }, 2000);
      }
    });
  };

  // ─── Download ────────────────────────────────────────────────────────────

  window.downloadArtifactContent = function () {
    if (!_currentContent || !_currentName) return;
    var filename = _currentName + (
      _currentVersion !== null ? '-v' + _currentVersion : ''
    ) + '.json';
    var blob = new Blob([_currentContent], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // ─── Human-readable file size ────────────────────────────────────────────

  function _humanSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(2) + ' MB';
  }

  // ─── Client-side search filter ───────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    var searchInput = document.getElementById('artifact-search');
    if (searchInput) {
      searchInput.addEventListener('keyup', function () {
        var q = this.value.toLowerCase().trim();
        var rows = document.querySelectorAll('#artifacts-table .artifact-row');
        rows.forEach(function (row) {
          var name = (row.dataset.name || '').toLowerCase();
          row.style.display = (!q || name.indexOf(q) !== -1) ? '' : 'none';
        });
      });
    }
  });

  // ─── Keyboard / outside-click close ─────────────────────────────────────

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      window.closeSlideover();
      // Also close legacy modals if still present
      var modalOverlay = document.getElementById('modal-overlay');
      if (modalOverlay) modalOverlay.style.display = 'none';
      var vModal = document.getElementById('version-modal');
      if (vModal) vModal.style.display = 'none';
    }
  });

  document.addEventListener('DOMContentLoaded', function () {
    var overlay = document.getElementById('slideover-overlay');
    if (overlay) {
      overlay.addEventListener('click', function () {
        window.closeSlideover();
      });
    }
  });

  // ─── Diff panel ──────────────────────────────────────────────────────────

  window.toggleDiffPanel = function () {
    var panel = document.getElementById('diff-panel');
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  };

  /**
   * Truncate a value for display in the diff table cell.
   * Long strings / complex objects are abbreviated with a tooltip.
   */
  function _truncateValue(val) {
    if (val === null) return '<span style="color:var(--text-dim);font-style:italic;">null</span>';
    if (val === undefined) return '<span style="color:var(--text-dim);font-style:italic;">—</span>';
    var str;
    if (typeof val === 'object') {
      str = JSON.stringify(val);
    } else {
      str = String(val);
    }
    var escaped = _esc(str);
    if (str.length > 120) {
      return '<span title="' + escaped + '">' + _esc(str.substring(0, 120)) + '…</span>';
    }
    return escaped;
  }

  /**
   * Build and insert rows into the diff table body.
   *
   * Row colour coding:
   *   added   — green  background  (key present in run2, missing from run1)
   *   removed — red    background  (key present in run1, missing from run2)
   *   changed — amber  background  (key in both runs, but values differ)
   *
   * @param {string[]} added   - keys present only in run2
   * @param {string[]} removed - keys present only in run1
   * @param {Array<{key:string,old:*,new:*}>} changed - keys with differing values
   */
  function _renderDiffTable(added, removed, changed) {
    var tbody = document.getElementById('diff-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    var totalRows = added.length + removed.length + changed.length;

    if (totalRows === 0) {
      var emptyRow = document.createElement('tr');
      emptyRow.innerHTML = '<td colspan="3" style="padding: 12px 10px; text-align: center; color: var(--text-dim); font-style: italic;">'
        + 'No differences — the artifacts are identical.</td>';
      tbody.appendChild(emptyRow);
      return;
    }

    // --- Added rows (green) ---
    added.forEach(function (key) {
      var tr = document.createElement('tr');
      tr.style.background = 'rgba(63, 185, 80, 0.12)';
      tr.setAttribute('data-diff-type', 'added');
      tr.innerHTML = '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: #3fb950; font-weight: 600;">'
        + _esc(key) + '</td>'
        + '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--text-dim); font-style: italic;">—</td>'
        + '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: #3fb950;">added</td>';
      tbody.appendChild(tr);
    });

    // --- Removed rows (red) ---
    removed.forEach(function (key) {
      var tr = document.createElement('tr');
      tr.style.background = 'rgba(248, 81, 73, 0.12)';
      tr.setAttribute('data-diff-type', 'removed');
      tr.innerHTML = '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: #f85149; font-weight: 600;">'
        + _esc(key) + '</td>'
        + '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: #f85149;">removed</td>'
        + '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--text-dim); font-style: italic;">—</td>';
      tbody.appendChild(tr);
    });

    // --- Changed rows (amber) ---
    changed.forEach(function (entry) {
      var tr = document.createElement('tr');
      tr.style.background = 'rgba(210, 153, 34, 0.12)';
      tr.setAttribute('data-diff-type', 'changed');
      tr.innerHTML = '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: #d29922; font-weight: 600;">'
        + _esc(entry.key || '') + '</td>'
        + '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--text);">'
        + _truncateValue(entry.old) + '</td>'
        + '<td style="padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--text);">'
        + _truncateValue(entry['new']) + '</td>';
      tbody.appendChild(tr);
    });
  }

  window.runDiff = function () {
    var run1 = (document.getElementById('diff-run1') || {}).value || '';
    var run2 = (document.getElementById('diff-run2') || {}).value || '';
    var nameEl = document.getElementById('diff-name');
    var name = nameEl ? (nameEl.value || '') : '';

    run1 = run1.trim(); run2 = run2.trim(); name = name.trim();

    var resultEl = document.getElementById('diff-result');
    var errorEl = document.getElementById('diff-error');
    if (resultEl) resultEl.style.display = 'none';
    if (errorEl) errorEl.style.display = 'none';

    if (!run1 || !run2 || !name) {
      if (errorEl) { errorEl.textContent = 'All three fields are required.'; errorEl.style.display = 'block'; }
      return;
    }

    var url = '/api/v1/artifacts/diff?run1=' + encodeURIComponent(run1)
      + '&run2=' + encodeURIComponent(run2)
      + '&name=' + encodeURIComponent(name);

    fetch(url)
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || 'HTTP ' + r.status); });
        return r.json();
      })
      .then(function (diff) {
        var added = diff.added || [];
        var removed = diff.removed || [];
        var changed = diff.changed || [];

        var summaryEl = document.getElementById('diff-summary');
        if (summaryEl) {
          summaryEl.textContent = 'Diff of \'' + name + '\': '
            + added.length + ' added, '
            + removed.length + ' removed, '
            + changed.length + ' changed  '
            + '(' + run1.substring(0, 12) + '… → ' + run2.substring(0, 12) + '…)';
        }

        _renderDiffTable(added, removed, changed);
        if (resultEl) resultEl.style.display = 'block';
      })
      .catch(function (err) {
        if (errorEl) { errorEl.textContent = 'Diff failed: ' + _esc(err.message); errorEl.style.display = 'block'; }
      });
  };

  // ─── Artifact search (global API search panel) ────────────────────────────

  window.runSearch = function () {
    var q = ((document.getElementById('search-q') || {}).value || '').trim();
    var type = ((document.getElementById('search-type') || {}).value || '').trim();
    var agent = ((document.getElementById('search-agent') || {}).value || '').trim();

    var params = new URLSearchParams();
    if (q) params.set('q', q);
    if (type) params.set('type', type);
    if (agent) params.set('agent', agent);

    var resultEl = document.getElementById('search-results');
    if (!resultEl) return;
    resultEl.innerHTML = '<p style="color:var(--text-dim)">Searching…</p>';

    fetch('/api/v1/artifacts/search?' + params.toString())
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || 'HTTP ' + r.status); });
        return r.json();
      })
      .then(function (results) {
        if (!results.length) {
          resultEl.innerHTML = '<p style="color:var(--text-dim)">No artifacts matched your query.</p>';
          return;
        }
        var html = '<table><thead><tr>'
          + '<th>Name</th><th>Run ID</th><th>Agent</th>'
          + '<th>Version</th><th>Size</th><th>Updated</th>'
          + '</tr></thead><tbody>';
        results.forEach(function (a) {
          html += '<tr>'
            + '<td><code>' + _esc(a.name) + '</code></td>'
            + '<td style="font-family:monospace;font-size:0.8rem">'
            + _esc((a.run_id || '—').substring(0, 16)) + '…</td>'
            + '<td>' + _esc(a.agent || '—') + '</td>'
            + '<td>v' + _esc(String(a.current_version)) + '</td>'
            + '<td>' + _esc(_humanSize(a.size_bytes || 0)) + '</td>'
            + '<td>' + _esc((a.updated_at || '—').substring(0, 19)) + '</td>'
            + '</tr>';
        });
        html += '</tbody></table>';
        resultEl.innerHTML = html;
      })
      .catch(function (err) {
        resultEl.innerHTML = '<p style="color:#f85149">Search error: ' + _esc(err.message) + '</p>';
      });
  };

  // ─── Backward-compat aliases (referenced from older inline handlers) ──────

  window.showVersionHistory = window.openSlideover;

}());
