import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/ws_event.dart';

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
  });
}
