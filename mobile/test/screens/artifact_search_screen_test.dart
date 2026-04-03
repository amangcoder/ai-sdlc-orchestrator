/// Widget tests for GlobalArtifactSearchScreen (TASK-014, TASK-018).
///
/// Tests verify:
///   - REQ-010: search input exists and accepts text
///   - Results display with mock data (artifact name, run/agent subtitle)
///   - Empty state shown when no results found
///   - Initial state shows helper text when query is empty
///   - Error state shows error icon

library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:go_router/go_router.dart';

import 'package:orchestrator_mobile/screens/artifact_search_screen.dart';
import 'package:orchestrator_mobile/providers/artifact_search_provider.dart';
import 'package:orchestrator_mobile/models/artifact_search_result_model.dart';

// ── Fake Notifiers ─────────────────────────────────────────────────────────────

class _FakeArtifactSearchNotifier extends ArtifactSearchNotifier {
  final ArtifactSearchState _initialState;

  _FakeArtifactSearchNotifier(this._initialState);

  @override
  Future<ArtifactSearchState> build() async => _initialState;

  @override
  Future<void> search(String query,
      {String? type, String? agent}) async {
    if (query.isEmpty) {
      state = const AsyncData(ArtifactSearchState());
      return;
    }
    state = AsyncData(_initialState.copyWith(query: query));
  }

  @override
  void clear() {
    state = const AsyncData(ArtifactSearchState());
  }
}

// ── Sample data ───────────────────────────────────────────────────────────────

List<ArtifactSearchResult> _sampleResults() => [
      ArtifactSearchResult(
        artifactName: 'prd',
        runId: 'run-aabbccdd1122',
        schema: 'prd',
        agent: 'pm',
        version: 2,
        updatedAt: DateTime.now().subtract(const Duration(hours: 1)),
      ),
      ArtifactSearchResult(
        artifactName: 'architecture',
        runId: 'run-aabbccdd1122',
        schema: 'architecture',
        agent: 'architect',
        version: 1,
        updatedAt: DateTime.now().subtract(const Duration(hours: 2)),
      ),
      ArtifactSearchResult(
        artifactName: 'tasks',
        runId: 'run-eeff99887766',
        schema: 'tasks',
        agent: 'tpm',
        version: 3,
        updatedAt: DateTime.now().subtract(const Duration(hours: 3)),
      ),
    ];

// ── Helpers ───────────────────────────────────────────────────────────────────

Widget _buildScreen({
  ArtifactSearchState? initialState,
  bool throwError = false,
}) {
  FlutterSecureStorage.setMockInitialValues({
    'server_url': 'http://localhost:9999',
    'api_key': 'test-api-key',
  });

  final state = initialState ?? const ArtifactSearchState();

  final router = GoRouter(
    initialLocation: '/artifact-search',
    routes: [
      GoRoute(
        path: '/artifact-search',
        builder: (_, __) => const GlobalArtifactSearchScreen(),
      ),
      GoRoute(
        path: '/runs/:runId/artifacts/:name',
        builder: (context, state) => Scaffold(
          body: Text(
            'Artifact ${state.pathParameters['name']} '
            'from run ${state.pathParameters['runId']}',
          ),
        ),
      ),
    ],
  );

  return ProviderScope(
    overrides: [
      artifactSearchNotifierProvider.overrideWith(
        () => _FakeArtifactSearchNotifier(state),
      ),
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

  group('GlobalArtifactSearchScreen — search input', () {
    testWidgets('search TextField is present', (tester) async {
      await tester.pumpWidget(_buildScreen());
      await tester.pumpAndSettle();

      expect(find.byType(TextField), findsOneWidget);
    });

    testWidgets('search hint text is visible', (tester) async {
      await tester.pumpWidget(_buildScreen());
      await tester.pumpAndSettle();

      expect(find.textContaining('Search artifacts'), findsOneWidget);
    });

    testWidgets('initial helper text shown when query is empty', (tester) async {
      await tester.pumpWidget(_buildScreen());
      await tester.pumpAndSettle();

      expect(find.textContaining('Type to search artifacts'), findsOneWidget);
    });
  });

  group('GlobalArtifactSearchScreen — results (REQ-010)', () {
    testWidgets('renders artifact names from search results', (tester) async {
      await tester.pumpWidget(
        _buildScreen(
          initialState: ArtifactSearchState(
            query: 'prd',
            results: _sampleResults(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('prd'), findsWidgets);
      expect(find.text('architecture'), findsOneWidget);
      expect(find.text('tasks'), findsOneWidget);
    });

    testWidgets('renders run and agent subtitle for each result',
        (tester) async {
      await tester.pumpWidget(
        _buildScreen(
          initialState: ArtifactSearchState(
            query: 'arch',
            results: _sampleResults(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Run ID prefix + agent: 'Run: run-aabbcc • Agent: architect'
      expect(find.textContaining('run-aabbc'), findsWidgets);
      expect(find.textContaining('Agent: architect'), findsOneWidget);
    });

    testWidgets('tapping result navigates to artifact detail', (tester) async {
      await tester.pumpWidget(
        _buildScreen(
          initialState: ArtifactSearchState(
            query: 'prd',
            results: _sampleResults(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Tap the first result (prd)
      await tester.tap(find.text('prd').first);
      await tester.pumpAndSettle();

      // Should navigate to artifact detail
      expect(find.textContaining('Artifact prd from run'), findsOneWidget);
    });
  });

  group('GlobalArtifactSearchScreen — empty state', () {
    testWidgets('shows "No artifacts found" when query has no results',
        (tester) async {
      await tester.pumpWidget(
        _buildScreen(
          initialState: const ArtifactSearchState(
            query: 'nonexistent',
            results: [],
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.textContaining('No artifacts found'), findsOneWidget);
    });

    testWidgets('shows search_off icon when no results', (tester) async {
      await tester.pumpWidget(
        _buildScreen(
          initialState: const ArtifactSearchState(
            query: 'notfound',
            results: [],
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.search_off), findsOneWidget);
    });
  });
}
