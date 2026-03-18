import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/phase_state.dart';

void main() {
  group('PhaseState.fromJson', () {
    test('deserializes all fields', () {
      final json = {
        'status': 'completed',
        'cost_usd': 2.50,
        'error': null,
        'model_tier': 'premium',
      };

      final phase = PhaseState.fromJson(json);
      expect(phase.status, 'completed');
      expect(phase.costUsd, 2.50);
      expect(phase.error, isNull);
      expect(phase.modelTier, 'premium');
    });

    test('defaults status to pending when absent', () {
      final json = <String, dynamic>{};
      final phase = PhaseState.fromJson(json);
      expect(phase.status, 'pending');
    });

    test('handles failed phase with error', () {
      final json = {
        'status': 'failed',
        'cost_usd': 0.10,
        'error': 'Agent timeout after 300 seconds',
        'model_tier': 'standard',
      };

      final phase = PhaseState.fromJson(json);
      expect(phase.status, 'failed');
      expect(phase.error, 'Agent timeout after 300 seconds');
    });

    test('handles null optional fields', () {
      final json = {
        'status': 'running',
      };

      final phase = PhaseState.fromJson(json);
      expect(phase.costUsd, isNull);
      expect(phase.error, isNull);
      expect(phase.modelTier, isNull);
    });

    test('handles integer cost_usd as double', () {
      final json = {
        'status': 'completed',
        'cost_usd': 5,
      };

      final phase = PhaseState.fromJson(json);
      expect(phase.costUsd, 5.0);
    });
  });
}
