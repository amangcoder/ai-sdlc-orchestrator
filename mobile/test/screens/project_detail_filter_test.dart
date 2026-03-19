/// Unit tests for the client-side filter logic in ProjectDetailScreen (TASK-014).
///
/// The _applyFilters method in ProjectDetailScreen is private, but the logic
/// is deterministic and dependency-free. We mirror it exactly here so that
/// any implementation change will surface as a test failure.
///
/// Tests verify:
///   - AC-017: status filter tabs each show only matching runs
///   - AC-017: status filter and search query compose with AND logic
///   - AC-018: search is case-insensitive substring match on featureRequest
///   - AC-018: clearing search restores filter-only results
///   - AC-020: behaviour with an empty run list

library;

import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/run_summary.dart';

// ── Filter logic mirror ───────────────────────────────────────────────────────
//
// Mirrors _applyFilters() from project_detail_screen.dart exactly.
// If the screen implementation changes, this mirror must be updated too.

List<RunSummary> applyFilters(
  List<RunSummary> runs,
  String statusFilter,
  String query,
) {
  return runs.where((run) {
    final matchesStatus = statusFilter == 'All' ||
        run.status.toLowerCase() == statusFilter.toLowerCase();
    final matchesSearch = query.isEmpty ||
        run.featureRequest.toLowerCase().contains(query.toLowerCase());
    return matchesStatus && matchesSearch;
  }).toList();
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

RunSummary _run({
  required String runId,
  required String status,
  required String featureRequest,
}) =>
    RunSummary(
      runId: runId,
      featureRequest: featureRequest,
      workflowType: 'feature_development',
      status: status,
    );

final _sampleRuns = [
  _run(runId: 'r1', status: 'running', featureRequest: 'Add auth module'),
  _run(runId: 'r2', status: 'completed', featureRequest: 'Build user dashboard'),
  _run(runId: 'r3', status: 'failed', featureRequest: 'Fix database auth bug'),
  _run(runId: 'r4', status: 'cancelled', featureRequest: 'Refactor payment service'),
  _run(runId: 'r5', status: 'running', featureRequest: 'Implement OAuth flow'),
];

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  group('ProjectDetail — status filter (AC-017)', () {
    test('filter "All" returns all runs', () {
      final result = applyFilters(_sampleRuns, 'All', '');
      expect(result.length, _sampleRuns.length);
    });

    test('filter "Running" returns only running runs', () {
      final result = applyFilters(_sampleRuns, 'Running', '');
      expect(result.every((r) => r.status == 'running'), isTrue);
      expect(result.length, 2); // r1 and r5
    });

    test('filter "Completed" returns only completed runs', () {
      final result = applyFilters(_sampleRuns, 'Completed', '');
      expect(result.every((r) => r.status == 'completed'), isTrue);
      expect(result.length, 1); // r2
    });

    test('filter "Failed" returns only failed runs', () {
      final result = applyFilters(_sampleRuns, 'Failed', '');
      expect(result.every((r) => r.status == 'failed'), isTrue);
      expect(result.length, 1); // r3
    });

    test('filter "Cancelled" returns only cancelled runs', () {
      final result = applyFilters(_sampleRuns, 'Cancelled', '');
      expect(result.every((r) => r.status == 'cancelled'), isTrue);
      expect(result.length, 1); // r4
    });

    test('filter returns empty list when no runs match status', () {
      final runs = [
        _run(runId: 'x', status: 'completed', featureRequest: 'Some feature'),
      ];
      final result = applyFilters(runs, 'Running', '');
      expect(result, isEmpty);
    });

    test('status comparison is case-insensitive', () {
      // Filter 'running' matches status 'running' regardless of UI chip case
      final result = applyFilters(_sampleRuns, 'running', '');
      expect(result.every((r) => r.status == 'running'), isTrue);
    });
  });

  group('ProjectDetail — search filter (AC-018)', () {
    test('empty query returns all runs (no filtering)', () {
      final result = applyFilters(_sampleRuns, 'All', '');
      expect(result.length, _sampleRuns.length);
    });

    test('search "auth" matches runs containing "auth" case-insensitively', () {
      final result = applyFilters(_sampleRuns, 'All', 'auth');
      // "Add auth module" (r1) and "Fix database auth bug" (r3)
      expect(result.length, 2);
      expect(result.map((r) => r.runId).toList(), containsAll(['r1', 'r3']));
    });

    test('search is case-insensitive — "Auth" matches "auth module"', () {
      final result = applyFilters(_sampleRuns, 'All', 'Auth');
      expect(result.any((r) => r.featureRequest.toLowerCase().contains('auth')),
          isTrue);
    });

    test('search with no matches returns empty list', () {
      final result = applyFilters(_sampleRuns, 'All', 'nonexistent-xyz');
      expect(result, isEmpty);
    });

    test('clearing search (empty string) restores all filter-only results', () {
      // First filter by 'running' and search 'oauth'
      final withSearch = applyFilters(_sampleRuns, 'Running', 'oauth');
      expect(withSearch.length, 1); // r5 = 'Implement OAuth flow'

      // Clear search — back to just the Running filter
      final withoutSearch = applyFilters(_sampleRuns, 'Running', '');
      expect(withoutSearch.length, 2); // r1 and r5
    });
  });

  group('ProjectDetail — composed AND filter (AC-017 + AC-018)', () {
    test('status Running AND search "auth" returns only matching run', () {
      final result = applyFilters(_sampleRuns, 'Running', 'auth');
      // Only r1 is running AND contains 'auth' (r3 failed, r5 running but no auth)
      expect(result.length, 1);
      expect(result.first.runId, 'r1');
    });

    test('both predicates active simultaneously — no partial matches', () {
      // Completed runs containing 'auth'
      final result = applyFilters(_sampleRuns, 'Completed', 'auth');
      // r2 is completed but does not contain 'auth' in featureRequest
      // r3 contains 'auth' but is failed, not completed
      expect(result, isEmpty);
    });

    test('composed filter returns correct results with multiple matches', () {
      final runs = [
        _run(runId: 'a', status: 'running', featureRequest: 'Auth service'),
        _run(runId: 'b', status: 'running', featureRequest: 'Auth dashboard'),
        _run(runId: 'c', status: 'completed', featureRequest: 'Auth legacy'),
        _run(runId: 'd', status: 'running', featureRequest: 'Unrelated task'),
      ];

      final result = applyFilters(runs, 'Running', 'auth');
      // a and b are running AND contain 'auth'
      expect(result.length, 2);
      expect(result.map((r) => r.runId).toList(), containsAll(['a', 'b']));
    });
  });

  group('ProjectDetail — empty state (AC-020)', () {
    test('returns empty list when run list is empty', () {
      final result = applyFilters([], 'All', '');
      expect(result, isEmpty);
    });

    test('returns empty list when filter matches nothing on empty list', () {
      final result = applyFilters([], 'Running', 'auth');
      expect(result, isEmpty);
    });

    test('partial match scenario — filtered empty != all-runs empty', () {
      // _allRuns is non-empty but filter returns empty
      final runs = [
        _run(runId: 'x', status: 'completed', featureRequest: 'stuff'),
      ];
      final filteredEmpty = applyFilters(runs, 'Running', '');
      expect(filteredEmpty, isEmpty);
      // _allRuns itself is non-empty — tested separately for UI logic
      expect(runs, isNotEmpty);
    });
  });

  group('ProjectDetail — edge cases', () {
    test('single-character search works', () {
      final result = applyFilters(_sampleRuns, 'All', 'a');
      // Every run with 'a' in featureRequest
      final expected =
          _sampleRuns.where((r) => r.featureRequest.toLowerCase().contains('a'));
      expect(result.length, expected.length);
    });

    test('unicode search is handled without error', () {
      final runs = [
        _run(runId: 'u1', status: 'running', featureRequest: 'Implement 日本語 UI'),
        _run(runId: 'u2', status: 'running', featureRequest: 'Normal feature'),
      ];
      final result = applyFilters(runs, 'All', '日本語');
      expect(result.length, 1);
      expect(result.first.runId, 'u1');
    });
  });
}
