/**
 * ReconnectingSSE — wraps the native EventSource API with automatic
 * reconnection, exponential backoff, and missed-event replay.
 *
 * Usage:
 *   const client = new ReconnectingSSE(url, {
 *     onEvent(data)      { /* called for every received event object *\/ },
 *     onDisconnect()     { /* called when connection drops *\/ },
 *     onReconnect()      { /* called after a successful reconnect *\/ },
 *     onEnd()            { /* called on stream_end / stream_timeout *\/ },
 *   });
 *
 *   // Graceful shutdown:
 *   client.close();
 *
 * Backoff schedule: 1 s → 2 s → 4 s → 8 s → 16 s → 30 s (capped).
 *
 * On disconnect a fixed-position warning banner is injected at the top of
 * the page body with id="sse-reconnect-banner".  The banner is removed once
 * the connection is re-established.
 *
 * On reconnect, missed events are fetched via:
 *   GET /api/v1/runs/{run_id}/events?offset=<eventCount>
 * and replayed through onEvent() in order before resuming the live SSE stream.
 */

/* UMD wrapper so the module can be required in Node (tests) or loaded as a
   plain <script> in browsers. */
(function (root, factory) {
  'use strict';
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.ReconnectingSSE = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ---------- Constants ---------- */

  /** Initial reconnection delay in milliseconds. */
  var BACKOFF_INITIAL = 1000;

  /** Maximum reconnection delay cap in milliseconds. */
  var BACKOFF_MAX = 30000;

  /* ---------- Constructor ---------- */

  /**
   * @constructor
   * @param {string} url - SSE endpoint URL.
   * @param {object} [options]
   * @param {function(object): void} [options.onEvent]      - Called for every event data object.
   * @param {function(): void}       [options.onDisconnect] - Called when the connection is lost.
   * @param {function(): void}       [options.onReconnect]  - Called after successful reconnect.
   * @param {function(): void}       [options.onEnd]        - Called on stream_end or stream_timeout.
   */
  function ReconnectingSSE(url, options) {
    if (!(this instanceof ReconnectingSSE)) {
      return new ReconnectingSSE(url, options);
    }

    options = options || {};

    /** @type {string} */
    this._url = url;

    /** @private */
    this._onEvent      = typeof options.onEvent      === 'function' ? options.onEvent      : function () {};
    /** @private */
    this._onDisconnect = typeof options.onDisconnect === 'function' ? options.onDisconnect : function () {};
    /** @private */
    this._onReconnect  = typeof options.onReconnect  === 'function' ? options.onReconnect  : function () {};
    /** @private */
    this._onEnd        = typeof options.onEnd        === 'function' ? options.onEnd        : function () {};

    /**
     * Total number of events received so far.
     * Used as the `offset` parameter when fetching missed events on reconnect.
     * @type {number}
     */
    this.eventCount = 0;

    /** @private {boolean} True once close() has been called. */
    this._closed = false;

    /** @private {number} Current backoff delay (ms) for the next attempt. */
    this._delay = BACKOFF_INITIAL;

    /** @private {number|null} Handle returned by setTimeout. */
    this._reconnTimer = null;

    /** @private {HTMLElement|null} The injected disconnection banner element. */
    this._banner = null;

    /** @private {EventSource|null} The active EventSource instance. */
    this._es = null;

    this._connect();
  }

  /* ---------- Private methods ---------- */

  /**
   * Open a new EventSource connection to this._url.
   * @private
   */
  ReconnectingSSE.prototype._connect = function () {
    if (this._closed) return;

    var self = this;
    var es   = new EventSource(this._url);
    this._es = es;

    es.onmessage = function (event) {
      var data;
      try {
        data = JSON.parse(event.data);
      } catch (_) {
        return;
      }

      /* Any successful message resets the backoff to the initial value. */
      self._delay = BACKOFF_INITIAL;

      self.eventCount++;
      self._onEvent(data);

      /* Terminal events: close the stream and fire onEnd. */
      if (data.event === 'stream_end' || data.event === 'stream_timeout') {
        es.close();
        self._es = null;
        self._onEnd();
      }
    };

    es.onerror = function () {
      es.close();
      self._es = null;
      if (self._closed) return;
      self._handleDisconnect();
    };
  };

  /**
   * Handle a connection drop: show the reconnection banner, fire onDisconnect,
   * and schedule the next reconnection attempt.
   * @private
   */
  ReconnectingSSE.prototype._handleDisconnect = function () {
    this._showBanner();
    this._onDisconnect();
    this._scheduleReconnect();
  };

  /**
   * Schedule the next reconnection attempt using the current backoff delay.
   * Doubles this._delay after scheduling (capped at BACKOFF_MAX).
   * @private
   */
  ReconnectingSSE.prototype._scheduleReconnect = function () {
    if (this._closed) return;

    var self  = this;
    var delay = this._delay;

    /* Advance the delay for the *next* attempt: 1→2→4→8→16→30 s. */
    this._delay = Math.min(this._delay * 2, BACKOFF_MAX);

    this._reconnTimer = setTimeout(function () {
      self._reconnTimer = null;
      self._doReconnect();
    }, delay);
  };

  /**
   * Fetch missed events (offset = this.eventCount), replay them through
   * onEvent, then re-open the SSE stream.
   * On fetch failure, schedules another attempt via _scheduleReconnect.
   * @private
   */
  ReconnectingSSE.prototype._doReconnect = function () {
    if (this._closed) return;

    var self      = this;
    var offset    = this.eventCount;
    var eventsUrl = this._buildEventsUrl(offset);

    fetch(eventsUrl)
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error('events fetch failed: ' + resp.status);
        }
        return resp.json();
      })
      .then(function (body) {
        /* Replay any missed events in order. */
        var missed = (body && Array.isArray(body.events)) ? body.events : [];
        for (var i = 0; i < missed.length; i++) {
          self.eventCount++;
          self._onEvent(missed[i]);
        }

        /* Reconnect succeeded: remove banner, notify caller, re-open stream. */
        self._removeBanner();
        self._onReconnect();
        self._connect();
      })
      .catch(function () {
        /* Fetch failed — schedule another attempt at the next backoff interval. */
        self._scheduleReconnect();
      });
  };

  /**
   * Derive the REST events URL from the SSE stream URL.
   *
   * Transforms:
   *   /api/runs/{run_id}/stream[?params]       → /api/v1/runs/{run_id}/events?[params&]offset=N
   *   /api/v1/runs/{run_id}/stream[?params]    → /api/v1/runs/{run_id}/events?[params&]offset=N
   *
   * Any existing query parameters from the original SSE URL (e.g. ?token=X)
   * are forwarded so that authentication is preserved.
   *
   * @param {number} offset
   * @returns {string}
   * @private
   */
  ReconnectingSSE.prototype._buildEventsUrl = function (offset) {
    var url  = this._url;

    /* Separate base path from query string. */
    var qIdx        = url.indexOf('?');
    var basePath    = qIdx !== -1 ? url.slice(0, qIdx) : url;
    var origQuery   = qIdx !== -1 ? url.slice(qIdx + 1) : '';

    /* Replace the terminal /stream segment with /events. */
    basePath = basePath.replace(/\/stream$/, '/events');

    /* Normalise /api/runs/ → /api/v1/runs/ for consistency. */
    basePath = basePath.replace(/^(\/api\/)(runs\/)/, '$1v1/$2');

    /* Build the final query string: forward original params + append offset. */
    var offsetParam = 'offset=' + encodeURIComponent(offset);
    var query       = origQuery ? origQuery + '&' + offsetParam : offsetParam;

    return basePath + '?' + query;
  };

  /**
   * Inject a fixed-position warning banner at the top of document.body.
   * No-ops if the banner is already present.
   * @private
   */
  ReconnectingSSE.prototype._showBanner = function () {
    if (this._banner) return;  /* Already visible. */

    var banner = document.createElement('div');
    banner.id  = 'sse-reconnect-banner';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    banner.setAttribute('aria-atomic', 'true');

    /* Inline styles guarantee visibility regardless of external CSS. */
    banner.style.cssText = [
      'position:fixed',
      'top:0',
      'left:0',
      'right:0',
      'z-index:9999',
      'padding:10px 16px',
      'background:#7d4e00',
      'color:#ffd166',
      'font-size:14px',
      'font-weight:600',
      'text-align:center',
      'box-shadow:0 2px 8px rgba(0,0,0,0.4)',
    ].join(';');

    /* \u2014 = em dash, \u2026 = horizontal ellipsis */
    banner.textContent = 'Connection lost \u2014 reconnecting\u2026';

    if (document.body) {
      document.body.insertBefore(banner, document.body.firstChild);
    }
    this._banner = banner;
  };

  /**
   * Remove the disconnection banner from the DOM.
   * @private
   */
  ReconnectingSSE.prototype._removeBanner = function () {
    if (this._banner && this._banner.parentNode) {
      this._banner.parentNode.removeChild(this._banner);
    }
    this._banner = null;
  };

  /* ---------- Public API ---------- */

  /**
   * Gracefully shut down the SSE client.
   * Cancels any pending reconnection timer, closes the active EventSource,
   * and removes the disconnection banner if visible.
   */
  ReconnectingSSE.prototype.close = function () {
    this._closed = true;

    if (this._reconnTimer !== null) {
      clearTimeout(this._reconnTimer);
      this._reconnTimer = null;
    }

    if (this._es) {
      this._es.close();
      this._es = null;
    }

    this._removeBanner();
  };

  return ReconnectingSSE;
}));
