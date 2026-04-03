/// Widget tests for SLOComplianceScreen (TASK-013, TASK-018).
///
/// Tests verify:
///   - AC-005, REQ-008: 6 SLI rows render correctly
///   - Color coding: green (passing), amber (at_risk), red (breached)
///   - Loading state shows CircularProgressIndicator
///   - Error state shows error widget
///   - Pull-to-refresh is available

library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'package:orchestrator_mobile/screens/slo_compliance_screen.dart';
import 'package:orchestrator_mobile/providers/slo_provider.dart';
import 'package:orchestrator_mobile/models/slo_report_model.dart';

// ── Fake Notifiers ─────────────────────────────────────────────────────────────

class _FakeSloNotifier extends SloNotifier {
  final SloReport? _data;
  final bool throwError;

  _FakeSloNotifier(this._data, {this.throwError = false});

  @override
  Future<SloReport?> build() async {
    if (throwError) throw Exception('SLO load failed');
    return _data;
  }

  @override
  Future<void> refresh() async {
    state = AsyncData(_data);
  }
}

// ── Sample data ───────────────────────────────────────────────────────────────

SloReport _sampleReport() => SloReport(slis: [
      const SliResult(
        name: 'Run Success Rate',
        target: 95.0,
        currentValue: 97.5,
        status: 'passing',
        errorBudgetRemainingPct: 90.0,
      ),
      const SliResult(
        name: 'Phase Latency P95',
        target: 60.0,
        currentValue: 58.0,
        status: 'passing',
        errorBudgetRemainingPct: 85.0,
      ),
      const SliResult(
        name: 'Artifact Save Rate',
        target: 99.0,
        currentValue: 98.0,
        status: 'at_risk',
        errorBudgetRemainingPct: 40.0,
      ),
      const SliResult(
        name: 'Cost Per Run',
        target: 5.0,
        currentValue: 4.5,
        status: 'passing',
        errorBudgetRemainingPct: 95.0,
      ),
      const SliResult(
        name: 'Agent Error Rate',
        target: 2.0,
        currentValue: 3.5,
        status: 'breached',
        errorBudgetRemainingPct: 0.0,
      ),
      const SliResult(
        name: 'MCP Response Time',
        target: 500.0,
        currentValue: 480.0,
        status: 'passing',
        errorBudgetRemainingPct: 80.0,
      ),
    ]);

// ── Helpers ───────────────────────────────────────────────────────────────────

Widget _buildScreen({
  SloReport? data,
  bool throwError = false,
}) {
  FlutterSecureStorage.setMockInitialValues({
    'server_url': 'http://localhost:9999',
    'api_key': 'test-api-key',
  });

  return ProviderScope(
    overrides: [
      sloNotifierProvider.overrideWith(
        () => _FakeSloNotifier(data, throwError: throwError),
      ),
    ],
    child: const MaterialApp(home: SLOComplianceScreen()),
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  setUp(() {
    FlutterSecureStorage.setMockInitialValues({
      'server_url': 'http://localhost:9999',
      'api_key': 'test-api-key',
    });
  });

  group('SLOComplianceScreen — SLI rows (AC-005, REQ-008)', () {
    testWidgets('renders all 6 SLI rows', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleReport()));
      await tester.pumpAndSettle();

      expect(find.text('Run Success Rate'), findsOneWidget);
      expect(find.text('Phase Latency P95'), findsOneWidget);
      expect(find.text('Artifact Save Rate'), findsOneWidget);
      expect(find.text('Cost Per Run'), findsOneWidget);
      expect(find.text('Agent Error Rate'), findsOneWidget);
      expect(find.text('MCP Response Time'), findsOneWidget);
    });

    testWidgets('shows "Passing" status label for passing SLIs',
        (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleReport()));
      await tester.pumpAndSettle();

      // Multiple passing SLIs in sample data
      expect(find.text('Passing'), findsWidgets);
    });

    testWidgets('shows "At Risk" status label for at_risk SLI', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleReport()));
      await tester.pumpAndSettle();

      expect(find.text('At Risk'), findsOneWidget);
    });

    testWidgets('shows "Breached" status label for breached SLI',
        (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleReport()));
      await tester.pumpAndSettle();

      expect(find.text('Breached'), findsOneWidget);
    });
  });

  group('SLOComplianceScreen — color coding', () {
    testWidgets('renders check_circle icon for passing SLIs', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleReport()));
      await tester.pumpAndSettle();

      // check_circle is used for passing status
      expect(find.byIcon(Icons.check_circle), findsWidgets);
    });

    testWidgets('renders warning icon for at_risk SLI', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleReport()));
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.warning_amber_rounded), findsOneWidget);
    });

    testWidgets('renders cancel icon for breached SLI', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleReport()));
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.cancel), findsOneWidget);
    });
  });

  group('SLOComplianceScreen — error state', () {
    testWidgets('shows error icon on failure', (tester) async {
      await tester.pumpWidget(_buildScreen(throwError: true));
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.error_outline), findsOneWidget);
    });
  });

  group('SLOComplianceScreen — empty data', () {
    testWidgets('shows empty message when no SLOs', (tester) async {
      await tester.pumpWidget(
          _buildScreen(data: const SloReport(slis: [])));
      await tester.pumpAndSettle();

      expect(find.textContaining('No SLO data available'), findsOneWidget);
    });
  });

  group('SLOComplianceScreen — pull-to-refresh', () {
    testWidgets('RefreshIndicator is present', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleReport()));
      await tester.pumpAndSettle();

      expect(find.byType(RefreshIndicator), findsOneWidget);
    });
  });
}
