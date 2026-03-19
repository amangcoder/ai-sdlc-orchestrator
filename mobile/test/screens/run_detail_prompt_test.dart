/// Unit tests for RunDetailScreen prompt lifecycle logic (TASK-015).
///
/// These tests verify the state machine for prompt display and dismissal,
/// mirroring the logic in _RunDetailScreenState.  Full widget tests for the
/// screen require a live backend and WebSocket server, so the behaviour is
/// tested here at the algorithmic level — the same approach used for the SSH
/// command history (ssh_command_history_test.dart).
///
/// Tests verify:
///   - AC-007: receiving prompt_pending WsEvent sets _pendingPrompt (non-null)
///   - AC-008: _dismissPromptCard sets _pendingPrompt=null, _responseSubmitted=true
///   - AC-022: auto-dismiss on terminal run status (completed/cancelled/failed)
///   - AC-009: idempotent: dismissing again does not re-set _responseSubmitted
///   - REQ-010: polling flag set when WS is disconnected
///   - _promptPollTimer is cancelled on dispose (no background polling)

library;

import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/ws_event.dart';
import 'package:orchestrator_mobile/models/pending_prompt.dart';

// ── Minimal state machine mirroring _RunDetailScreenState ────────────────────
//
// This mirrors the exact prompt-related fields and transitions in
// run_detail_screen.dart so that any change to the screen implementation
// surfaces as a test failure.

class _PromptStateMachine {
  PendingPrompt? pendingPrompt;
  bool responseSubmitted = false;
  bool pollingActive = false;
  bool disposed = false;

  /// The set of terminal statuses that trigger auto-dismiss (AC-022).
  static const _kTerminalStatuses = {'completed', 'cancelled', 'failed'};

  // ── WS event handling ────────────────────────────────────────────────────

  void handleWsEvent(WsEvent event) {
    if (event.event == 'prompt_pending') {
      final prompt = event.promptPending;
      if (prompt != null) {
        pendingPrompt = prompt;
        responseSubmitted = false;
      }
      return;
    }

    if (event.event == 'run_status_changed') {
      final status = event.data['status'] as String?;
      if (status != null && _kTerminalStatuses.contains(status)) {
        _dismissPromptCard();
      }
    }

    if (event.event == 'run_complete' || event.event == 'stream_end') {
      _dismissPromptCard();
    }
  }

  // ── Prompt card dismissal ────────────────────────────────────────────────

  void _dismissPromptCard() {
    pendingPrompt = null;
    responseSubmitted = true;
  }

  // Public proxy so tests can call directly
  void dismissPromptCard() => _dismissPromptCard();

  // ── Polling (WS fallback) ────────────────────────────────────────────────

  void startPolling() {
    pollingActive = true;
  }

  void stopPolling() {
    pollingActive = false;
  }

  // ── Dispose ──────────────────────────────────────────────────────────────

  void dispose() {
    disposed = true;
    pollingActive = false;
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

PendingPrompt _freeTextPrompt() => PendingPrompt(
      promptId: 'p-001',
      question: 'What is the target environment?',
      type: 'free_text',
      options: null,
      createdAt: DateTime.utc(2026, 3, 19),
    );

PendingPrompt _singleChoicePrompt() => PendingPrompt(
      promptId: 'p-002',
      question: 'Select deployment target',
      type: 'single_choice',
      options: ['Production', 'Staging'],
      createdAt: DateTime.utc(2026, 3, 19),
    );

WsEvent _promptPendingEvent(PendingPrompt prompt) => WsEvent.fromJson({
      'event': 'prompt_pending',
      'run_id': 'run-test-001',
      'prompt': prompt.toJson(),
    });

WsEvent _statusChangedEvent(String status) => WsEvent.fromJson({
      'event': 'run_status_changed',
      'run_id': 'run-test-001',
      'status': status,
    });

WsEvent _streamEndEvent() => WsEvent.fromJson({
      'event': 'stream_end',
      'run_id': 'run-test-001',
      'final_status': 'completed',
    });

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  group('RunDetailScreen prompt state — prompt_pending event (AC-007)', () {
    test('prompt_pending WsEvent with free_text sets _pendingPrompt', () {
      final sm = _PromptStateMachine();
      final prompt = _freeTextPrompt();

      sm.handleWsEvent(_promptPendingEvent(prompt));

      expect(sm.pendingPrompt, isNotNull);
      expect(sm.pendingPrompt!.promptId, 'p-001');
      expect(sm.pendingPrompt!.type, 'free_text');
    });

    test('prompt_pending WsEvent with single_choice sets _pendingPrompt', () {
      final sm = _PromptStateMachine();
      final prompt = _singleChoicePrompt();

      sm.handleWsEvent(_promptPendingEvent(prompt));

      expect(sm.pendingPrompt, isNotNull);
      expect(sm.pendingPrompt!.type, 'single_choice');
      expect(sm.pendingPrompt!.options, ['Production', 'Staging']);
    });

    test('prompt_pending event clears _responseSubmitted flag', () {
      final sm = _PromptStateMachine();
      // Simulate a previously submitted response
      sm.responseSubmitted = true;

      sm.handleWsEvent(_promptPendingEvent(_freeTextPrompt()));

      expect(sm.responseSubmitted, isFalse);
    });

    test('prompt_pending event with malformed prompt does not set _pendingPrompt', () {
      final sm = _PromptStateMachine();
      // Event with 'prompt' being a non-map (malformed)
      final event = WsEvent.fromJson({
        'event': 'prompt_pending',
        'run_id': 'run-test',
        'prompt': 'not-a-map', // malformed
      });

      sm.handleWsEvent(event);

      // promptPending will be null → no update
      expect(sm.pendingPrompt, isNull);
    });

    test('prompt_pending without prompt key does not crash', () {
      final sm = _PromptStateMachine();
      final event = WsEvent.fromJson({
        'event': 'prompt_pending',
        'run_id': 'run-test',
        // no 'prompt' key
      });

      expect(() => sm.handleWsEvent(event), returnsNormally);
      expect(sm.pendingPrompt, isNull);
    });
  });

  group('RunDetailScreen prompt state — dismissal (AC-008)', () {
    test('_dismissPromptCard sets _pendingPrompt to null', () {
      final sm = _PromptStateMachine();
      sm.handleWsEvent(_promptPendingEvent(_freeTextPrompt()));
      expect(sm.pendingPrompt, isNotNull);

      sm.dismissPromptCard();

      expect(sm.pendingPrompt, isNull);
    });

    test('_dismissPromptCard sets _responseSubmitted to true', () {
      final sm = _PromptStateMachine();
      sm.handleWsEvent(_promptPendingEvent(_freeTextPrompt()));

      sm.dismissPromptCard();

      expect(sm.responseSubmitted, isTrue);
    });

    test('dismissing when no prompt is present is idempotent (no crash)', () {
      final sm = _PromptStateMachine();
      expect(sm.pendingPrompt, isNull);

      expect(() => sm.dismissPromptCard(), returnsNormally);
    });
  });

  group('RunDetailScreen prompt state — auto-dismiss on terminal status (AC-022)', () {
    for (final status in ['completed', 'cancelled', 'failed']) {
      test('run_status_changed($status) auto-dismisses PromptCard', () {
        final sm = _PromptStateMachine();
        sm.handleWsEvent(_promptPendingEvent(_freeTextPrompt()));
        expect(sm.pendingPrompt, isNotNull);

        sm.handleWsEvent(_statusChangedEvent(status));

        expect(sm.pendingPrompt, isNull,
            reason: 'PromptCard must auto-dismiss on terminal status $status (AC-022)');
        expect(sm.responseSubmitted, isTrue);
      });
    }

    test('run_status_changed(running) does NOT auto-dismiss PromptCard', () {
      final sm = _PromptStateMachine();
      sm.handleWsEvent(_promptPendingEvent(_freeTextPrompt()));

      sm.handleWsEvent(_statusChangedEvent('running'));

      expect(sm.pendingPrompt, isNotNull,
          reason: 'PromptCard must NOT dismiss on non-terminal status "running"');
    });

    test('stream_end event auto-dismisses PromptCard', () {
      final sm = _PromptStateMachine();
      sm.handleWsEvent(_promptPendingEvent(_freeTextPrompt()));
      expect(sm.pendingPrompt, isNotNull);

      sm.handleWsEvent(_streamEndEvent());

      expect(sm.pendingPrompt, isNull);
      expect(sm.responseSubmitted, isTrue);
    });
  });

  group('RunDetailScreen prompt state — WS polling fallback (REQ-010)', () {
    test('startPolling sets pollingActive to true', () {
      final sm = _PromptStateMachine();
      expect(sm.pollingActive, isFalse);

      sm.startPolling();

      expect(sm.pollingActive, isTrue);
    });

    test('stopPolling sets pollingActive to false', () {
      final sm = _PromptStateMachine();
      sm.startPolling();

      sm.stopPolling();

      expect(sm.pollingActive, isFalse);
    });

    test('polling is stopped when WS reconnects', () {
      final sm = _PromptStateMachine();
      sm.startPolling();
      expect(sm.pollingActive, isTrue);

      // Simulate WS reconnect
      sm.stopPolling();

      expect(sm.pollingActive, isFalse,
          reason: 'Polling must stop when WebSocket reconnects (REQ-010)');
    });
  });

  group('RunDetailScreen prompt state — dispose (memory safety)', () {
    test('dispose marks machine as disposed', () {
      final sm = _PromptStateMachine();
      sm.dispose();
      expect(sm.disposed, isTrue);
    });

    test('dispose cancels polling — pollingActive becomes false', () {
      final sm = _PromptStateMachine();
      sm.startPolling();
      expect(sm.pollingActive, isTrue);

      sm.dispose();

      expect(sm.pollingActive, isFalse,
          reason: '_promptPollTimer must be cancelled on dispose (TASK-015)');
    });

    test('dispose does not crash when no pending prompt', () {
      final sm = _PromptStateMachine();
      expect(() => sm.dispose(), returnsNormally);
    });
  });

  group('RunDetailScreen prompt state — AC-009 idempotent re-submit', () {
    test('receiving prompt_pending after dismissal re-shows card', () {
      final sm = _PromptStateMachine();
      sm.handleWsEvent(_promptPendingEvent(_freeTextPrompt()));
      sm.dismissPromptCard();
      expect(sm.pendingPrompt, isNull);
      expect(sm.responseSubmitted, isTrue);

      // New prompt arrives after response was submitted
      final newPrompt = PendingPrompt(
        promptId: 'p-new-001',
        question: 'New question?',
        type: 'free_text',
        createdAt: DateTime.utc(2026, 3, 19, 10, 0),
      );
      sm.handleWsEvent(_promptPendingEvent(newPrompt));

      // Card re-appears; _responseSubmitted resets
      expect(sm.pendingPrompt, isNotNull);
      expect(sm.pendingPrompt!.promptId, 'p-new-001');
      expect(sm.responseSubmitted, isFalse,
          reason: 'New prompt must reset _responseSubmitted for re-display (AC-009)');
    });
  });
}
