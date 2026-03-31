/**
 * settings.js — Settings page interactivity
 *
 * Responsibilities:
 *  1. Tab switching (no page reload)
 *  2. Load config from GET /api/v1/config and populate form fields
 *  3. "Save Changes" — serialize only changed fields, PUT /api/v1/config
 *  4. Inline validation with field-level error messages
 *  5. Toast notifications (success green / error red)
 *  6. GC flow: Preview (POST /api/v1/artifacts/retention?dry_run=true)
 *             then Run GC (confirmation dialog + POST with dry_run=false)
 */

(function () {
  'use strict';

  // ── Toast ─────────────────────────────────────────────────────────────────

  /**
   * Show a toast notification.
   * @param {string} message
   * @param {'success'|'error'|'info'} type
   * @param {number} [duration=3000] ms before auto-dismiss
   */
  function showToast(message, type, duration) {
    var el = document.getElementById('toast-notification');
    if (!el) return;
    clearTimeout(el._dismissTimer);

    el.textContent = message;
    el.className = 'toast toast-' + (type || 'info') + ' toast-visible';
    el.removeAttribute('hidden');
    el.setAttribute('aria-live', type === 'error' ? 'assertive' : 'polite');

    el._dismissTimer = setTimeout(function () {
      el.className = 'toast';
      el.hidden = true;
    }, duration || 3000);
  }

  // ── Tab switching ─────────────────────────────────────────────────────────

  function initTabs() {
    var tabBtns = document.querySelectorAll('.settings-tab');
    var tabPanels = document.querySelectorAll('.settings-tab-panel');

    tabBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var target = btn.getAttribute('data-tab');

        tabBtns.forEach(function (b) {
          b.classList.remove('active');
          b.setAttribute('aria-selected', 'false');
        });
        tabPanels.forEach(function (p) {
          p.hidden = true;
        });

        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');

        var panel = document.getElementById('tab-' + target);
        if (panel) panel.hidden = false;
      });
    });
  }

  // ── Config loading ────────────────────────────────────────────────────────

  /** Snapshot of config as loaded from server — used to detect changes. */
  var _originalConfig = {};

  /**
   * Recursively get a deeply-nested value from an object.
   * path is a dot-separated string, e.g. "monitoring.slo.enabled".
   */
  function getNestedValue(obj, path) {
    var parts = path.split('.');
    var cur = obj;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== 'object') return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }

  /**
   * Set a deeply-nested value in an object (mutates).
   * e.g. setNestedValue(obj, "monitoring.slo.enabled", true)
   */
  function setNestedValue(obj, path, value) {
    var parts = path.split('.');
    var cur = obj;
    for (var i = 0; i < parts.length - 1; i++) {
      if (cur[parts[i]] == null || typeof cur[parts[i]] !== 'object') {
        cur[parts[i]] = {};
      }
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = value;
  }

  /** Populate all form fields from a config object. */
  function populateFields(config) {
    var inputs = document.querySelectorAll('[data-original]');
    inputs.forEach(function (input) {
      var path = input.name;
      if (!path) return;
      var val = getNestedValue(config, path);
      if (val === undefined || val === null) return;

      if (input.type === 'checkbox') {
        input.checked = Boolean(val);
        input.dataset.original = String(Boolean(val));
      } else if (input.tagName === 'SELECT') {
        input.value = String(val);
        input.dataset.original = String(val);
      } else {
        input.value = String(val);
        input.dataset.original = String(val);
      }
    });
  }

  /** Fetch config from server and populate form. */
  function loadConfig() {
    var indicator = document.getElementById('save-indicator');
    if (indicator) indicator.textContent = 'Loading config…';

    fetch('/api/v1/config')
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (cfg) {
        _originalConfig = cfg || {};
        populateFields(_originalConfig);
        if (indicator) indicator.textContent = '';
      })
      .catch(function (err) {
        if (indicator) indicator.textContent = 'Failed to load config';
        showToast('Failed to load configuration: ' + err.message, 'error', 5000);
      });
  }

  // ── Validation ────────────────────────────────────────────────────────────

  var _urlPattern = /^https?:\/\/.+/;

  /** Show or hide a field error message. */
  function setFieldError(inputEl, message) {
    var errId = 'err-' + inputEl.id;
    var errEl = document.getElementById(errId);
    if (message) {
      inputEl.classList.add('invalid');
      if (errEl) {
        errEl.textContent = message;
        errEl.hidden = false;
      }
    } else {
      inputEl.classList.remove('invalid');
      if (errEl) {
        errEl.textContent = '';
        errEl.hidden = true;
      }
    }
  }

  /** Clear all field errors. */
  function clearAllErrors() {
    document.querySelectorAll('.form-input.invalid').forEach(function (el) {
      el.classList.remove('invalid');
    });
    document.querySelectorAll('.field-error').forEach(function (el) {
      el.textContent = '';
      el.hidden = true;
    });
  }

  /**
   * Validate a single input element.
   * Returns an error string or empty string if valid.
   */
  function validateInput(input) {
    var val = input.value.trim();
    var name = input.name || '';

    // URL fields — allow empty (optional fields) but validate format if non-empty
    if (input.type === 'url' || name.endsWith('_url') || name.endsWith('_endpoint')) {
      if (val !== '' && !_urlPattern.test(val)) {
        return 'Must be a valid URL starting with http:// or https://';
      }
    }

    // Number fields
    if (input.type === 'number' && val !== '') {
      var num = parseFloat(val);
      if (isNaN(num)) return 'Must be a valid number';
      var min = input.min !== '' ? parseFloat(input.min) : null;
      var max = input.max !== '' ? parseFloat(input.max) : null;
      if (min !== null && num < min) return 'Must be at least ' + min;
      if (max !== null && num > max) return 'Must be at most ' + max;
    }

    return '';
  }

  /** Validate all visible form inputs. Returns true if all pass. */
  function validateAll() {
    clearAllErrors();
    var allValid = true;
    var inputs = document.querySelectorAll('#settings-form input, #settings-form select');
    inputs.forEach(function (input) {
      if (!input.name) return;
      var err = validateInput(input);
      if (err) {
        setFieldError(input, err);
        allValid = false;
      }
    });
    return allValid;
  }

  // Live validation on blur
  function initLiveValidation() {
    document.querySelectorAll('#settings-form input[name], #settings-form select[name]').forEach(function (input) {
      input.addEventListener('blur', function () {
        var err = validateInput(input);
        setFieldError(input, err);
      });
      input.addEventListener('input', function () {
        // Clear error on input so the user gets immediate feedback
        if (input.classList.contains('invalid')) {
          var err = validateInput(input);
          setFieldError(input, err);
        }
      });
    });
  }

  // ── Changed-fields serialization ──────────────────────────────────────────

  /**
   * Read current form values and return only fields that changed
   * relative to _originalConfig.
   *
   * Returns a nested object suitable for PUT /api/v1/config.
   * Top-level sections (e.g. "monitoring", "artifacts") are included
   * as full section dicts when any nested field changed.
   */
  function serializeChangedFields() {
    var changed = {};
    var inputs = document.querySelectorAll('#settings-form [data-original]');

    inputs.forEach(function (input) {
      var path = input.name;
      if (!path) return;

      var current;
      if (input.type === 'checkbox') {
        current = input.checked;
      } else if (input.type === 'number') {
        var raw = input.value.trim();
        if (raw === '') return; // skip empty optional numbers
        current = Number(raw);
        if (isNaN(current)) return;
      } else {
        current = input.value.trim();
      }

      var original = getNestedValue(_originalConfig, path);
      // Normalize original for comparison
      if (input.type === 'checkbox') {
        original = Boolean(original);
      } else if (input.type === 'number') {
        original = original !== undefined ? Number(original) : undefined;
      } else {
        original = original !== undefined ? String(original) : undefined;
      }

      // Skip if unchanged
      if (current === original) return;
      // Skip empty URL fields if original was also absent/null
      if (input.type !== 'checkbox' && current === '' && (original === undefined || original === null || original === '')) return;

      setNestedValue(changed, path, current);
    });

    return changed;
  }

  /**
   * Merge the top-level sections of `changed` with the full original
   * section from _originalConfig so the server receives a complete
   * section dict (shallow merge per section).
   */
  function buildPayload(changed) {
    var payload = {};
    var topKeys = Object.keys(changed);

    topKeys.forEach(function (key) {
      var val = changed[key];
      if (val !== null && typeof val === 'object' && !Array.isArray(val)) {
        // Merge with original section
        var originalSection = (_originalConfig[key] !== null && typeof _originalConfig[key] === 'object')
          ? JSON.parse(JSON.stringify(_originalConfig[key]))
          : {};
        // Deep-merge changed sub-keys into original section
        deepMerge(originalSection, val);
        payload[key] = originalSection;
      } else {
        payload[key] = val;
      }
    });
    return payload;
  }

  /** Simple recursive merge of src into dst (mutates dst). */
  function deepMerge(dst, src) {
    Object.keys(src).forEach(function (k) {
      if (src[k] !== null && typeof src[k] === 'object' && !Array.isArray(src[k])) {
        if (dst[k] == null || typeof dst[k] !== 'object') dst[k] = {};
        deepMerge(dst[k], src[k]);
      } else {
        dst[k] = src[k];
      }
    });
  }

  // ── Save handler ──────────────────────────────────────────────────────────

  function initSaveForm() {
    var form = document.getElementById('settings-form');
    if (!form) return;

    form.addEventListener('submit', function (e) {
      e.preventDefault();

      if (!validateAll()) {
        showToast('Please fix validation errors before saving.', 'error');
        return;
      }

      var changed = serializeChangedFields();
      if (Object.keys(changed).length === 0) {
        showToast('No changes to save.', 'info');
        return;
      }

      var payload = buildPayload(changed);
      var saveBtn = document.getElementById('save-btn');
      var indicator = document.getElementById('save-indicator');

      if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving…';
      }
      if (indicator) indicator.textContent = '';

      fetch('/api/v1/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(function (res) {
          return res.json().then(function (body) {
            return { ok: res.ok, status: res.status, body: body };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            var msg = (result.body && result.body.error) ? result.body.error : 'Save failed (HTTP ' + result.status + ')';
            showToast(msg, 'error', 6000);
          } else {
            showToast('Configuration saved', 'success', 3000);
            // Reload config to update original snapshots
            loadConfig();
          }
        })
        .catch(function (err) {
          showToast('Network error: ' + err.message, 'error', 6000);
        })
        .finally(function () {
          if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Changes';
          }
        });
    });

    // Reset button
    var resetBtn = document.getElementById('reset-btn');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        clearAllErrors();
        populateFields(_originalConfig);
        showToast('Reset to saved values.', 'info', 2000);
      });
    }
  }

  // ── GC flow ───────────────────────────────────────────────────────────────

  /** State shared between preview and run GC */
  var _gcPreviewData = null;

  /** Get current retention params from form inputs. */
  function getRetentionParams() {
    var maxAgeDays = parseInt(document.getElementById('artifacts_retention_max_age_days').value || '90', 10);
    var maxRuns = parseInt(document.getElementById('artifacts_retention_max_runs').value || '200', 10);
    var keepFailed = document.getElementById('artifacts_retention_keep_failed').checked;

    if (isNaN(maxAgeDays) || maxAgeDays < 1) maxAgeDays = 90;
    if (isNaN(maxRuns) || maxRuns < 1) maxRuns = 200;

    return { max_age_days: maxAgeDays, max_runs: maxRuns, keep_failed: keepFailed };
  }

  function initGC() {
    var previewBtn = document.getElementById('gc-preview-btn');
    var runBtn = document.getElementById('gc-run-btn');
    var statusEl = document.getElementById('gc-status');
    var resultsEl = document.getElementById('gc-preview-results');
    var candidateCountEl = document.getElementById('gc-candidate-count');
    var retainedCountEl = document.getElementById('gc-retained-count');
    var pathsContainer = document.getElementById('gc-paths-container');
    var pathsList = document.getElementById('gc-paths-list');

    if (!previewBtn || !runBtn) return;

    previewBtn.addEventListener('click', function () {
      var params = getRetentionParams();
      var body = Object.assign({}, params, { dry_run: true });

      previewBtn.disabled = true;
      previewBtn.textContent = 'Previewing…';
      if (statusEl) statusEl.textContent = '';
      if (resultsEl) resultsEl.hidden = true;

      // Disable run button while preview is in-flight
      runBtn.disabled = true;
      runBtn.setAttribute('aria-disabled', 'true');

      fetch('/api/v1/artifacts/retention', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, status: res.status, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            var msg = (result.data && result.data.detail) ? result.data.detail : 'Preview failed (HTTP ' + result.status + ')';
            if (statusEl) statusEl.textContent = msg;
            showToast('GC preview failed: ' + msg, 'error', 5000);
            return;
          }

          _gcPreviewData = result.data;

          var deletedCount = result.data.deleted_versions || 0;
          var retainedCount = result.data.retained_versions || 0;
          var paths = result.data.deleted_paths || [];

          if (candidateCountEl) candidateCountEl.textContent = deletedCount;
          if (retainedCountEl) retainedCountEl.textContent = retainedCount;

          if (pathsList) {
            if (paths.length > 0) {
              pathsList.innerHTML = paths.map(function (p) {
                return '<div style="padding: 2px 0; border-bottom: 1px solid var(--border);">' + escapeHtml(p) + '</div>';
              }).join('');
              if (pathsContainer) pathsContainer.style.display = '';
            } else {
              if (pathsContainer) pathsContainer.style.display = 'none';
            }
          }

          if (resultsEl) resultsEl.hidden = false;

          if (deletedCount === 0) {
            if (statusEl) statusEl.textContent = 'Nothing to delete — retention policy satisfied.';
            // Run GC still disabled since there's nothing to do
          } else {
            if (statusEl) statusEl.textContent = deletedCount + ' version(s) eligible for deletion.';
            runBtn.disabled = false;
            runBtn.setAttribute('aria-disabled', 'false');
          }
        })
        .catch(function (err) {
          showToast('Network error during GC preview: ' + err.message, 'error', 5000);
          if (statusEl) statusEl.textContent = 'Preview failed.';
        })
        .finally(function () {
          previewBtn.disabled = false;
          previewBtn.textContent = 'Preview GC';
        });
    });

    runBtn.addEventListener('click', function () {
      if (!_gcPreviewData) return;

      var deletedCount = _gcPreviewData.deleted_versions || 0;
      var confirmed = window.confirm(
        'This will permanently delete ' + deletedCount + ' artifact version' + (deletedCount !== 1 ? 's' : '') + '. Continue?'
      );
      if (!confirmed) return;

      var params = getRetentionParams();
      var body = Object.assign({}, params, { dry_run: false });

      runBtn.disabled = true;
      runBtn.setAttribute('aria-disabled', 'true');
      previewBtn.disabled = true;
      if (statusEl) statusEl.textContent = 'Running GC…';

      fetch('/api/v1/artifacts/retention', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Confirm-Retention-Delete': 'yes',
        },
        body: JSON.stringify(body),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, status: res.status, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            var msg = (result.data && result.data.detail) ? result.data.detail : 'GC failed (HTTP ' + result.status + ')';
            showToast('GC failed: ' + msg, 'error', 6000);
            if (statusEl) statusEl.textContent = 'GC failed.';
            return;
          }
          var actualDeleted = result.data.deleted_versions || 0;
          showToast('GC complete — deleted ' + actualDeleted + ' artifact version(s).', 'success', 4000);
          if (statusEl) statusEl.textContent = 'GC complete. ' + actualDeleted + ' version(s) deleted.';

          // Reset preview state
          _gcPreviewData = null;
          if (resultsEl) resultsEl.hidden = true;
          runBtn.disabled = true;
          runBtn.setAttribute('aria-disabled', 'true');
        })
        .catch(function (err) {
          showToast('Network error during GC: ' + err.message, 'error', 6000);
          if (statusEl) statusEl.textContent = 'GC failed.';
        })
        .finally(function () {
          previewBtn.disabled = false;
          previewBtn.textContent = 'Preview GC';
        });
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Boot ──────────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    initTabs();
    initLiveValidation();
    initSaveForm();
    initGC();
    loadConfig();
  });

  // Expose for testing
  window._settingsPage = {
    loadConfig: loadConfig,
    showToast: showToast,
    validateAll: validateAll,
    serializeChangedFields: serializeChangedFields,
    buildPayload: buildPayload,
    getNestedValue: getNestedValue,
    setNestedValue: setNestedValue,
  };
}());
