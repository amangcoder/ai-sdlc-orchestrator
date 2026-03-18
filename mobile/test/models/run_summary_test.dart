import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/run_summary.dart';

void main() {
  group('RunSummary.fromJson', () {
    test('deserializes all fields correctly', () {
      final json = {
        'run_id': 'abc123def456',
        'feature_request': 'Build a dashboard',
        'workflow_type': 'feature_development',
        'status': 'running',
        'current_step': 'prd',
        'total_cost_usd': 12.34,
        'start_time': '2026-03-18T10:00:00.000Z',
        'end_time': '2026-03-18T11:00:00.000Z',
        'steps_completed': 3,
        'steps_total': 10,
      };

      final summary = RunSummary.fromJson(json);

      expect(summary.runId, 'abc123def456');
      expect(summary.featureRequest, 'Build a dashboard');
      expect(summary.workflowType, 'feature_development');
      expect(summary.status, 'running');
      expect(summary.currentStep, 'prd');
      expect(summary.totalCostUsd, 12.34);
      expect(summary.startTime, isA<DateTime>());
      expect(summary.endTime, isA<DateTime>());
      expect(summary.stepsCompleted, 3);
      expect(summary.stepsTotal, 10);
    });

    test('handles nullable current_step as null', () {
      final json = {
        'run_id': 'abc123',
        'feature_request': 'test',
        'workflow_type': 'bugfix',
        'status': 'completed',
      };

      final summary = RunSummary.fromJson(json);
      expect(summary.currentStep, isNull);
    });

    test('handles nullable totalCostUsd', () {
      final json = {
        'run_id': 'abc123',
        'status': 'running',
      };

      final summary = RunSummary.fromJson(json);
      expect(summary.totalCostUsd, isNull);
    });

    test('handles nullable start_time and end_time', () {
      final json = {
        'run_id': 'abc123',
        'status': 'pending',
      };

      final summary = RunSummary.fromJson(json);
      expect(summary.startTime, isNull);
      expect(summary.endTime, isNull);
    });

    test('defaults steps to 0 when absent', () {
      final json = {
        'run_id': 'abc123',
        'status': 'running',
      };

      final summary = RunSummary.fromJson(json);
      expect(summary.stepsCompleted, 0);
      expect(summary.stepsTotal, 0);
    });

    test('defaults empty strings for missing text fields', () {
      final json = {
        'run_id': 'abc123',
      };

      final summary = RunSummary.fromJson(json);
      expect(summary.featureRequest, '');
      expect(summary.workflowType, '');
      expect(summary.status, 'unknown');
    });

    test('handles integer total_cost_usd as double', () {
      final json = {
        'run_id': 'abc123',
        'total_cost_usd': 5,
      };

      final summary = RunSummary.fromJson(json);
      expect(summary.totalCostUsd, 5.0);
    });

    test('toJson round-trips correctly', () {
      final original = RunSummary(
        runId: 'test123',
        featureRequest: 'Build it',
        workflowType: 'refactor',
        status: 'completed',
        currentStep: 'qa',
        totalCostUsd: 42.50,
        startTime: DateTime.utc(2026, 3, 18, 10),
        endTime: DateTime.utc(2026, 3, 18, 11),
        stepsCompleted: 8,
        stepsTotal: 10,
      );

      final json = original.toJson();
      final restored = RunSummary.fromJson(json);

      expect(restored.runId, original.runId);
      expect(restored.featureRequest, original.featureRequest);
      expect(restored.status, original.status);
      expect(restored.currentStep, original.currentStep);
      expect(restored.totalCostUsd, original.totalCostUsd);
    });
  });
}
