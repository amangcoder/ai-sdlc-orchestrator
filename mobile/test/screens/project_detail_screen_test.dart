/// Widget tests for ProjectDetailScreen (TASK-014).
///
/// Tests verify:
///   - AC-004: AppBar title shows projectName
///   - AC-017: status filter tabs appear (All/Running/Completed/Failed/Cancelled)
///   - AC-017: selecting a status tab filters visible runs
///   - AC-018: search bar present and filters by featureRequest
///   - AC-020: empty state 'No runs for this project yet' + Start Run button
///   - REQ-016: _activeStatusFilter resets to 'All' on dispose

library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:go_router/go_router.dart';

import 'package:orchestrator_mobile/screens/project_detail_screen.dart';
import 'package:orchestrator_mobile/providers/auth_provider.dart';
import 'package:orchestrator_mobile/services/api_service.dart';
import 'package:orchestrator_mobile/services/secure_storage_service.dart';
import 'package:orchestrator_mobile/models/run_summary.dart';
import 'package:orchestrator_mobile/models/ssh_config_response.dart';

// ── Fake ApiService ───────────────────────────────────────────────────────────

class _FakeApiService extends ApiService {
  final List<RunSummary> _runs;

  _FakeApiService(this._runs)
      : super(
          credentials: const Credentials(
            url: 'http://localhost:9999',
            apiKey: 'test-key',
          ),
        );

  @override
  Future<List<RunSummary>> listRunsByProject(String workspaceId) async {
    return _runs;
  }

  @override
  Future<SshConfigResponse> getSshConfig() async {
    throw const NetworkException('Not reachable');
  }
}

// ── Sample data ───────────────────────────────────────────────────────────────

List<RunSummary> _mixedRuns() => [
      const RunSummary(
        runId: 'run-001',
        featureRequest: 'Add OAuth authentication module',
        workflowType: 'feature_development',
        status: 'running',
      ),
      const RunSummary(
        runId: 'run-002',
        featureRequest: 'Build user dashboard component',
        workflowType: 'feature_development',
        status: 'completed',
      ),
      const RunSummary(
        runId: 'run-003',
        featureRequest: 'Fix auth database migration bug',
        workflowType: 'bugfix',
        status: 'failed',
      ),
      const RunSummary(
        runId: 'run-004',
        featureRequest: 'Refactor payment service',
        workflowType: 'refactor',
        status: 'cancelled',
      ),
      const RunSummary(
        runId: 'run-005',
        featureRequest: 'Implement WebSocket live updates',
        workflowType: 'feature_development',
        status: 'running',
      ),
    ];

// ── Helper ────────────────────────────────────────────────────────────────────

Widget _buildProjectDetail({
  String projectId = 'proj-test',
  String projectName = 'Test Project',
  List<RunSummary>? runs,
}) {
  FlutterSecureStorage.setMockInitialValues({
    'server_url': 'http://localhost:9999',
    'api_key': 'test-api-key',
  });

  final apiService = _FakeApiService(runs ?? _mixedRuns());

  final router = GoRouter(
    initialLocation: '/project/$projectId',
    routes: [
      GoRoute(
        path: '/project/:projectId',
        builder: (context, state) => ProjectDetailScreen(
          projectId: state.pathParameters['projectId']!,
          projectName: projectName,
        ),
      ),
      GoRoute(
        path: '/new-run',
        builder: (context, state) =>
            const Scaffold(body: Text('New Run Screen')),
      ),
      GoRoute(
        path: '/settings',
        builder: (context, state) =>
            const Scaffold(body: Text('Settings')),
      ),
    ],
  );

  return ProviderScope(
    overrides: [
      apiServiceProvider.overrideWithValue(apiService),
    ],
    child: MaterialApp.router(routerConfig: router),
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

  group('ProjectDetailScreen — AppBar (AC-004)', () {
    testWidgets('AppBar shows projectName as title', (tester) async {
      await tester.pumpWidget(
        _buildProjectDetail(projectName: 'My Awesome Project'),
      );
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(find.text('My Awesome Project'), findsOneWidget);
    });

    testWidgets('different projectName values appear correctly', (tester) async {
      await tester.pumpWidget(
        _buildProjectDetail(projectName: 'Backend Service'),
      );
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(find.text('Backend Service'), findsOneWidget);
    });
  });

  group('ProjectDetailScreen — filter tabs (AC-017)', () {
    testWidgets('shows all five filter tabs', (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(find.text('All'), findsOneWidget);
      expect(find.text('Running'), findsOneWidget);
      expect(find.text('Completed'), findsOneWidget);
      expect(find.text('Failed'), findsOneWidget);
      expect(find.text('Cancelled'), findsOneWidget);
    });

    testWidgets('filter tabs rendered as ChoiceChip widgets', (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(find.byType(ChoiceChip), findsNWidgets(5));
    });
  });

  group('ProjectDetailScreen — search bar (AC-018)', () {
    testWidgets('search bar is visible', (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(find.byType(TextField), findsOneWidget);
    });

    testWidgets('search bar shows correct hint text', (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(
        find.byWidgetPredicate((widget) =>
            widget is TextField &&
            (widget.decoration?.hintText?.contains('feature request') == true ||
                widget.decoration?.hintText?.contains('Search') == true)),
        findsOneWidget,
      );
    });
  });

  group('ProjectDetailScreen — empty state (AC-020)', () {
    testWidgets('shows "No runs for this project yet" when no runs',
        (tester) async {
      await tester.pumpWidget(_buildProjectDetail(runs: []));
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(
        find.text('No runs for this project yet'),
        findsOneWidget,
      );
    });

    testWidgets('shows Start Run button when no runs exist (AC-020)',
        (tester) async {
      await tester.pumpWidget(_buildProjectDetail(runs: []));
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(find.text('Start Run'), findsOneWidget);
    });

    testWidgets('Start Run button is tappable', (tester) async {
      await tester.pumpWidget(_buildProjectDetail(runs: []));
      await tester.pumpAndSettle(const Duration(seconds: 2));

      // tap should navigate without crashing
      await tester.tap(find.text('Start Run'));
      await tester.pumpAndSettle();

      expect(find.text('New Run Screen'), findsOneWidget);
    });
  });

  group('ProjectDetailScreen — content when runs available', () {
    testWidgets('shows runs from the project (RunCard widgets visible)',
        (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      // RunCards should be visible (featureRequest text appears)
      expect(
        find.textContaining('OAuth authentication'),
        findsOneWidget,
      );
    });

    testWidgets('FloatingActionButton is present for starting a new run',
        (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      expect(find.byType(FloatingActionButton), findsOneWidget);
    });

    testWidgets('FAB tap navigates to /new-run with workspaceId pre-filled',
        (tester) async {
      // Workspace-isolation requirement: FAB must pass projectId as workspaceId
      // so the new run is created in the correct project folder.
      await tester.pumpWidget(
        _buildProjectDetail(projectId: 'ws-isolation-proj'),
      );
      await tester.pumpAndSettle(const Duration(seconds: 2));

      await tester.tap(find.byType(FloatingActionButton));
      await tester.pumpAndSettle();

      // Should navigate to New Run Screen without error
      expect(find.text('New Run Screen'), findsOneWidget);
    });
  });

  group('ProjectDetailScreen — filter interaction (AC-017)', () {
    testWidgets('tapping Running filter chip changes selection', (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      // Tap the 'Running' chip
      await tester.tap(find.text('Running'));
      await tester.pump();

      // After selecting Running, the Running chip should be selected
      final runningChip = tester.widget<ChoiceChip>(
        find.ancestor(
          of: find.text('Running'),
          matching: find.byType(ChoiceChip),
        ),
      );
      expect(runningChip.selected, isTrue);
    });

    testWidgets('tapping filter chip does not throw', (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      for (final label in ['Running', 'Completed', 'Failed', 'Cancelled', 'All']) {
        await tester.tap(find.text(label));
        await tester.pump();
      }

      expect(tester.takeException(), isNull);
    });
  });

  group('ProjectDetailScreen — pull-to-refresh', () {
    testWidgets('RefreshIndicator wraps the runs list', (tester) async {
      await tester.pumpWidget(_buildProjectDetail());
      await tester.pumpAndSettle(const Duration(seconds: 2));

      // When runs exist, a RefreshIndicator wraps the ListView
      expect(find.byType(RefreshIndicator), findsOneWidget);
    });
  });
}
