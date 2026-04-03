/// Widget tests for AlertsScreen (TASK-014, TASK-018).
///
/// Tests verify:
///   - REQ-009: alerts list renders with severity icons
///   - Severity filter chips are visible
///   - Empty state shown when no alerts
///   - Error state shows error icon
///   - Pull-to-refresh is available

library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'package:orchestrator_mobile/screens/alerts_screen.dart';
import 'package:orchestrator_mobile/providers/alerts_provider.dart';
import 'package:orchestrator_mobile/models/alert_model.dart';

// ── Fake Notifiers ─────────────────────────────────────────────────────────────

class _FakeAlertsNotifier extends AlertsNotifier {
  final List<Alert> _alerts;
  final bool throwError;

  _FakeAlertsNotifier(this._alerts, {this.throwError = false});

  @override
  Future<List<Alert>> build() async {
    if (throwError) throw Exception('Alerts load failed');
    return _alerts;
  }

  @override
  Future<void> refresh() async {
    state = AsyncData(_alerts);
  }
}

// ── Sample data ───────────────────────────────────────────────────────────────

List<Alert> _sampleAlerts() => [
      Alert(
        id: 'alert-001',
        severity: 'critical',
        message: 'Orchestrator run failed after 3 retries',
        triggeredAt: DateTime.now().subtract(const Duration(minutes: 5)),
        status: 'active',
      ),
      Alert(
        id: 'alert-002',
        severity: 'warning',
        message: 'Cost per run exceeds warning threshold',
        triggeredAt: DateTime.now().subtract(const Duration(hours: 1)),
        status: 'acknowledged',
      ),
      Alert(
        id: 'alert-003',
        severity: 'info',
        message: 'New run started successfully',
        triggeredAt: DateTime.now().subtract(const Duration(hours: 2)),
        status: 'resolved',
      ),
    ];

// ── Helpers ───────────────────────────────────────────────────────────────────

Widget _buildScreen({
  List<Alert>? alerts,
  bool throwError = false,
}) {
  FlutterSecureStorage.setMockInitialValues({
    'server_url': 'http://localhost:9999',
    'api_key': 'test-api-key',
  });

  return ProviderScope(
    overrides: [
      alertsNotifierProvider.overrideWith(
        () => _FakeAlertsNotifier(alerts ?? _sampleAlerts(),
            throwError: throwError),
      ),
    ],
    child: const MaterialApp(home: AlertsScreen()),
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

  group('AlertsScreen — alerts list (REQ-009)', () {
    testWidgets('renders alert messages', (tester) async {
      await tester.pumpWidget(_buildScreen());
      await tester.pumpAndSettle();

      expect(find.text('Orchestrator run failed after 3 retries'),
          findsOneWidget);
      expect(find.text('Cost per run exceeds warning threshold'), findsOneWidget);
      expect(find.text('New run started successfully'), findsOneWidget);
    });

    testWidgets('renders severity icons for all alert severities',
        (tester) async {
      await tester.pumpWidget(_buildScreen());
      await tester.pumpAndSettle();

      // Critical → Icons.error
      expect(find.byIcon(Icons.error), findsOneWidget);
      // Warning → Icons.warning_amber_rounded
      expect(find.byIcon(Icons.warning_amber_rounded), findsOneWidget);
      // Info → Icons.info_outline
      expect(find.byIcon(Icons.info_outline), findsOneWidget);
    });
  });

  group('AlertsScreen — filter chips', () {
    testWidgets('shows All, Critical, Warning, Info filter chips',
        (tester) async {
      await tester.pumpWidget(_buildScreen());
      await tester.pumpAndSettle();

      expect(find.text('All'), findsOneWidget);
      expect(find.text('Critical'), findsOneWidget);
      expect(find.text('Warning'), findsOneWidget);
      expect(find.text('Info'), findsOneWidget);
    });

    testWidgets('tapping Critical filter shows only critical alerts',
        (tester) async {
      await tester.pumpWidget(_buildScreen());
      await tester.pumpAndSettle();

      await tester.tap(find.text('Critical'));
      await tester.pumpAndSettle();

      expect(find.text('Orchestrator run failed after 3 retries'),
          findsOneWidget);
      expect(
          find.text('Cost per run exceeds warning threshold'), findsNothing);
      expect(find.text('New run started successfully'), findsNothing);
    });
  });

  group('AlertsScreen — empty state', () {
    testWidgets('shows "No alerts." when list is empty', (tester) async {
      await tester.pumpWidget(_buildScreen(alerts: []));
      await tester.pumpAndSettle();

      expect(find.text('No alerts.'), findsOneWidget);
    });
  });

  group('AlertsScreen — error state', () {
    testWidgets('shows error icon on failure', (tester) async {
      await tester.pumpWidget(_buildScreen(throwError: true));
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.error_outline), findsOneWidget);
    });
  });

  group('AlertsScreen — pull-to-refresh', () {
    testWidgets('RefreshIndicator is present', (tester) async {
      await tester.pumpWidget(_buildScreen());
      await tester.pumpAndSettle();

      expect(find.byType(RefreshIndicator), findsOneWidget);
    });
  });
}
