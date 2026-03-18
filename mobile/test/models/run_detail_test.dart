import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/run_detail.dart';
import 'package:orchestrator_mobile/models/phase_state.dart';

void main() {
  group('RunDetail.fromJson', () {
    test('deserializes phases correctly', () {
      final json = {
        'run_id': 'abc123',
        'feature_request': 'Build dashboard',
        'workflow_type': 'feature_development',
        'status': 'running',
        'phases': {
          'prd': {
            'status': 'completed',
            'cost_usd': 1.23,
            'model_tier': 'premium',
          },
          'architecture': {
            'status': 'running',
          },
        },
        'workflow_tasks': [
          {'task': 'prd', 'status': 'done'},
        ],
      };

      final detail = RunDetail.fromJson(json);

      expect(detail.runId, 'abc123');
      expect(detail.phases.length, 2);
      expect(detail.phases['prd']!.status, 'completed');
      expect(detail.phases['prd']!.costUsd, 1.23);
      expect(detail.phases['prd']!.modelTier, 'premium');
      expect(detail.phases['architecture']!.status, 'running');
      expect(detail.workflowTasks.length, 1);
    });

    test('handles empty phases', () {
      final json = {
        'run_id': 'abc123',
        'status': 'pending',
      };

      final detail = RunDetail.fromJson(json);
      expect(detail.phases, isEmpty);
      expect(detail.workflowTasks, isEmpty);
    });

    test('handles null phases', () {
      final json = {
        'run_id': 'abc123',
        'status': 'pending',
        'phases': null,
      };

      final detail = RunDetail.fromJson(json);
      expect(detail.phases, isEmpty);
    });
  });
}
