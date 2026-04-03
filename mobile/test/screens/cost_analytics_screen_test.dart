/// Widget tests for CostAnalyticsScreen (TASK-013, TASK-018).
///
/// Tests verify:
///   - AC-004, REQ-007: KPI cards render with mock data
///   - Cost-by-agent list renders correct number of items
///   - Loading state shows CircularProgressIndicator
///   - Error state shows error widget
///   - Empty data shows "No cost analytics data available"
///   - Pull-to-refresh is available

library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'package:orchestrator_mobile/screens/cost_analytics_screen.dart';
import 'package:orchestrator_mobile/providers/cost_analytics_provider.dart';
import 'package:orchestrator_mobile/models/cost_analytics_model.dart';
import 'package:orchestrator_mobile/widgets/error_state_widget.dart';

// ── Fake Notifiers ─────────────────────────────────────────────────────────────

class _FakeCostAnalyticsNotifier extends CostAnalyticsNotifier {
  final CostAnalytics? _data;
  final bool throwError;

  _FakeCostAnalyticsNotifier(this._data, {this.throwError = false});

  @override
  Future<CostAnalytics?> build() async {
    if (throwError) throw Exception('Cost analytics load failed');
    return _data;
  }

  @override
  Future<void> refresh() async {
    state = AsyncData(_data);
  }
}

// ── Sample data ───────────────────────────────────────────────────────────────

CostAnalytics _sampleAnalytics() => CostAnalytics(
      totalSpendUsd: 12.34,
      burnRateUsdPerHour: 0.456,
      avgCostPerRun: 1.23,
      costByAgent: [
        const AgentCost(agent: 'pm', costUsd: 2.00),
        const AgentCost(agent: 'architect', costUsd: 3.50),
        const AgentCost(agent: 'backend_engineer', costUsd: 5.00),
      ],
      costByModel: [
        const ModelCost(model: 'claude-3-5-sonnet', costUsd: 10.50),
        const ModelCost(model: 'claude-3-haiku', costUsd: 1.84),
      ],
    );

// ── Helpers ───────────────────────────────────────────────────────────────────

Widget _buildScreen({
  CostAnalytics? data,
  bool loading = false,
  bool throwError = false,
}) {
  FlutterSecureStorage.setMockInitialValues({
    'server_url': 'http://localhost:9999',
    'api_key': 'test-api-key',
  });

  return ProviderScope(
    overrides: [
      costAnalyticsNotifierProvider.overrideWith(
        () => _FakeCostAnalyticsNotifier(data, throwError: throwError),
      ),
    ],
    child: const MaterialApp(home: CostAnalyticsScreen()),
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

  group('CostAnalyticsScreen — KPI cards (AC-004, REQ-007)', () {
    testWidgets('renders Total Spend KPI card', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleAnalytics()));
      await tester.pumpAndSettle();

      expect(find.text('Total Spend'), findsOneWidget);
      expect(find.textContaining('\$12.34'), findsWidgets);
    });

    testWidgets('renders Burn Rate KPI card', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleAnalytics()));
      await tester.pumpAndSettle();

      expect(find.text('Burn Rate'), findsOneWidget);
    });

    testWidgets('renders Avg / Run KPI card', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleAnalytics()));
      await tester.pumpAndSettle();

      expect(find.text('Avg / Run'), findsOneWidget);
    });
  });

  group('CostAnalyticsScreen — cost-by-agent list', () {
    testWidgets('renders correct number of agent cost rows', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleAnalytics()));
      await tester.pumpAndSettle();

      // 3 agents in sample data
      expect(find.text('pm'), findsOneWidget);
      expect(find.text('architect'), findsOneWidget);
      expect(find.text('backend_engineer'), findsOneWidget);
    });

    testWidgets('renders Cost by Agent section header', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleAnalytics()));
      await tester.pumpAndSettle();

      expect(find.text('Cost by Agent'), findsOneWidget);
    });

    testWidgets('renders Cost by Model section header', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleAnalytics()));
      await tester.pumpAndSettle();

      expect(find.text('Cost by Model'), findsOneWidget);
    });
  });

  group('CostAnalyticsScreen — loading state', () {
    testWidgets('shows CircularProgressIndicator while loading',
        (tester) async {
      // Use a notifier that never resolves during pump
      FlutterSecureStorage.setMockInitialValues({
        'server_url': 'http://localhost:9999',
        'api_key': 'test-api-key',
      });

      final completer = Future<CostAnalytics?>.delayed(
        const Duration(seconds: 10),
        () => _sampleAnalytics(),
      );

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            costAnalyticsNotifierProvider.overrideWith(
              () => _FakeSlowNotifier(completer),
            ),
          ],
          child: const MaterialApp(home: CostAnalyticsScreen()),
        ),
      );

      // Don't settle — check during loading
      await tester.pump();
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
    });
  });

  group('CostAnalyticsScreen — error state', () {
    testWidgets('shows error widget on failure', (tester) async {
      await tester.pumpWidget(_buildScreen(throwError: true));
      await tester.pumpAndSettle();

      // ErrorStateWidget is rendered for error states
      expect(find.byType(ErrorStateWidget), findsOneWidget);
      expect(find.byIcon(Icons.error_outline), findsOneWidget);
    });
  });

  group('CostAnalyticsScreen — null data', () {
    testWidgets('shows empty message when data is null', (tester) async {
      await tester.pumpWidget(_buildScreen(data: null));
      await tester.pumpAndSettle();

      expect(find.textContaining('No cost analytics data available'), findsOneWidget);
    });
  });

  group('CostAnalyticsScreen — pull-to-refresh', () {
    testWidgets('RefreshIndicator is present', (tester) async {
      await tester.pumpWidget(_buildScreen(data: _sampleAnalytics()));
      await tester.pumpAndSettle();

      expect(find.byType(RefreshIndicator), findsOneWidget);
    });
  });
}

// Helper: notifier that stays loading
class _FakeSlowNotifier extends CostAnalyticsNotifier {
  final Future<CostAnalytics?> _future;
  _FakeSlowNotifier(this._future);

  @override
  Future<CostAnalytics?> build() => _future;
}
