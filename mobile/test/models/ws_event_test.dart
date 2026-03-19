import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/ws_event.dart';
import 'package:orchestrator_mobile/models/pending_prompt.dart';

void main() {
  group('WsEvent.fromJson', () {
    test('deserializes event with all fields', () {
      final json = {
        'event': 'task_result',
        'ts': '2026-03-18T10:30:00.000Z',
        'step': 'prd',
        'agent_role': 'pm',
        'cost_usd': 0.45,
        'model_tier': 'premium',
      };

      final event = WsEvent.fromJson(json);
      expect(event.event, 'task_result');
      expect(event.ts, '2026-03-18T10:30:00.000Z');
      expect(event.data['step'], 'prd');
      expect(event.data['agent_role'], 'pm');
      expect(event.data['cost_usd'], 0.45);
    });

    test('handles heartbeat event', () {
      final json = {
        'event': 'heartbeat',
        'ts': '2026-03-18T10:30:00.000Z',
      };

      final event = WsEvent.fromJson(json);
      expect(event.event, 'heartbeat');
      expect(event.ts, isNotNull);
      // Heartbeat should have only event and ts in data
      expect(event.data.containsKey('event'), true);
    });

    test('handles missing event field', () {
      final json = <String, dynamic>{
        'ts': '2026-03-18T10:30:00.000Z',
      };

      final event = WsEvent.fromJson(json);
      expect(event.event, '');
    });

    test('handles missing ts field', () {
      final json = {
        'event': 'run_start',
      };

      final event = WsEvent.fromJson(json);
      expect(event.ts, isNull);
    });

    test('preserves all data fields', () {
      final json = {
        'event': 'task_invoke',
        'ts': '2026-03-18T10:00:00Z',
        'step': 'architecture',
        'agent_role': 'architect',
        'model_tier': 'standard',
        'custom_field': 'custom_value',
      };

      final event = WsEvent.fromJson(json);
      expect(event.data['custom_field'], 'custom_value');
      expect(event.data['step'], 'architecture');
    });

    test('stream_end event has run_id and final_status', () {
      final json = {
        'event': 'stream_end',
        'run_id': 'abc123',
        'final_status': 'completed',
      };

      final event = WsEvent.fromJson(json);
      expect(event.event, 'stream_end');
      expect(event.data['run_id'], 'abc123');
      expect(event.data['final_status'], 'completed');
    });

    // ── Defensive 'type' fallback (TASK-004 forward-compat) ─────────────────

    test('falls back to json["type"] when json["event"] is absent', () {
      final json = <String, dynamic>{
        'type': 'run_status_changed',
        'ts': '2026-03-19T10:00:00Z',
        'status': 'running',
      };

      final event = WsEvent.fromJson(json);
      expect(event.event, 'run_status_changed');
    });

    test('prefers json["event"] over json["type"] when both present', () {
      final json = <String, dynamic>{
        'event': 'log_line',
        'type': 'something_else',
        'ts': '2026-03-19T10:00:00Z',
      };

      final event = WsEvent.fromJson(json);
      expect(event.event, 'log_line');
    });

    test('defaults event to empty string when neither event nor type present', () {
      final json = <String, dynamic>{
        'ts': '2026-03-19T10:00:00Z',
        'data': 'some_payload',
      };

      final event = WsEvent.fromJson(json);
      expect(event.event, '');
    });
  });

  // ── prompt_pending event and promptPending getter (TASK-004) ────────────────

  group('WsEvent.promptPending', () {
    test('returns PendingPrompt for prompt_pending event with valid prompt', () {
      final json = {
        'event': 'prompt_pending',
        'run_id': 'run-123',
        'prompt': {
          'prompt_id': 'ppp-001',
          'question': 'Confirm the tech stack?',
          'type': 'single_choice',
          'options': ['React', 'Flutter', 'Vue'],
          'created_at': '2026-03-19T10:00:00Z',
        },
      };

      final event = WsEvent.fromJson(json);

      expect(event.event, 'prompt_pending');
      expect(event.promptPending, isNotNull);
      expect(event.promptPending, isA<PendingPrompt>());
      expect(event.promptPending!.promptId, 'ppp-001');
      expect(event.promptPending!.question, 'Confirm the tech stack?');
      expect(event.promptPending!.type, 'single_choice');
      expect(event.promptPending!.options, ['React', 'Flutter', 'Vue']);
      expect(event.isPromptPending, isTrue);
    });

    test('returns PendingPrompt for prompt_pending free_text with null options', () {
      final json = {
        'event': 'prompt_pending',
        'run_id': 'run-456',
        'prompt': {
          'prompt_id': 'ppp-002',
          'question': 'Describe the expected behavior',
          'type': 'free_text',
          'options': null,
          'created_at': '2026-03-19T11:00:00Z',
        },
      };

      final event = WsEvent.fromJson(json);

      expect(event.promptPending, isNotNull);
      expect(event.promptPending!.type, 'free_text');
      expect(event.promptPending!.options, isNull);
    });

    test('returns null for non-prompt_pending events — no regression', () {
      final eventTypes = [
        'run_status_changed',
        'log_line',
        'task_result',
        'phase_start',
        'phase_complete',
        'heartbeat',
        'stream_end',
        'task_invoke',
      ];

      for (final eventType in eventTypes) {
        final json = {
          'event': eventType,
          'ts': '2026-03-19T10:00:00Z',
        };
        final event = WsEvent.fromJson(json);
        expect(
          event.promptPending,
          isNull,
          reason: 'promptPending should be null for event type "$eventType"',
        );
        expect(event.isPromptPending, isFalse);
      }
    });

    test('returns null when event is prompt_pending but prompt key is missing', () {
      final json = {
        'event': 'prompt_pending',
        'run_id': 'run-789',
        // no 'prompt' key
      };

      final event = WsEvent.fromJson(json);
      expect(event.promptPending, isNull);
      expect(event.isPromptPending, isFalse);
    });

    test('returns null when prompt value is not a map', () {
      final json = {
        'event': 'prompt_pending',
        'run_id': 'run-abc',
        'prompt': 'malformed-string-not-a-map',
      };

      final event = WsEvent.fromJson(json);
      expect(event.promptPending, isNull);
    });

    test('returns null when prompt map is malformed but does not throw', () {
      // Missing required fields — fromJson handles gracefully
      final json = {
        'event': 'prompt_pending',
        'prompt': <String, dynamic>{},
      };

      // Should not throw; PendingPrompt.fromJson uses safe defaults
      expect(() => WsEvent.fromJson(json).promptPending, returnsNormally);
    });

    test('prompt_pending via "type" fallback key also works', () {
      // Forward-compat: backend might emit 'type' instead of 'event'
      final json = {
        'type': 'prompt_pending',
        'run_id': 'run-type-fallback',
        'prompt': {
          'prompt_id': 'ppp-003',
          'question': 'Use new API?',
          'type': 'single_choice',
          'options': ['Yes', 'No'],
          'created_at': '2026-03-19T12:00:00Z',
        },
      };

      final event = WsEvent.fromJson(json);
      expect(event.event, 'prompt_pending');
      expect(event.promptPending, isNotNull);
      expect(event.promptPending!.options, ['Yes', 'No']);
    });
  });
}
