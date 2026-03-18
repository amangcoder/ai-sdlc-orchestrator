import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'providers/auth_provider.dart';
import 'screens/dashboard_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/run_detail_screen.dart';
import 'screens/live_events_screen.dart';
import 'screens/artifact_viewer_screen.dart';
import 'screens/new_run_screen.dart';
import 'screens/config_editor_screen.dart';

/// Root widget that configures GoRouter with auth-based redirect logic.
class OrchestratorApp extends ConsumerStatefulWidget {
  const OrchestratorApp({super.key});

  @override
  ConsumerState<OrchestratorApp> createState() => _OrchestratorAppState();
}

class _OrchestratorAppState extends ConsumerState<OrchestratorApp> {
  late final GoRouter _router;

  @override
  void initState() {
    super.initState();
    _router = GoRouter(
      initialLocation: '/',
      redirect: (context, state) {
        final authState = ref.read(authNotifierProvider);
        // While loading, don't redirect
        if (authState.isLoading) return null;
        final hasCredentials = authState.valueOrNull != null;
        final isOnSettings = state.matchedLocation == '/settings';

        if (!hasCredentials && !isOnSettings) {
          return '/settings';
        }
        if (hasCredentials && isOnSettings) {
          return '/';
        }
        return null;
      },
      routes: [
        GoRoute(
          path: '/',
          name: 'dashboard',
          builder: (context, state) => const DashboardScreen(),
        ),
        GoRoute(
          path: '/settings',
          name: 'settings',
          builder: (context, state) => const SettingsScreen(),
        ),
        GoRoute(
          path: '/new-run',
          name: 'new-run',
          builder: (context, state) => const NewRunScreen(),
        ),
        GoRoute(
          path: '/config',
          name: 'config',
          builder: (context, state) => const ConfigEditorScreen(),
        ),
        GoRoute(
          path: '/runs/:runId',
          name: 'run-detail',
          builder: (context, state) => RunDetailScreen(
            runId: state.pathParameters['runId']!,
          ),
          routes: [
            GoRoute(
              path: 'events',
              name: 'live-events',
              builder: (context, state) => LiveEventsScreen(
                runId: state.pathParameters['runId']!,
              ),
            ),
            GoRoute(
              path: 'artifacts',
              name: 'artifact-list',
              builder: (context, state) => ArtifactViewerScreen(
                runId: state.pathParameters['runId']!,
              ),
              routes: [
                GoRoute(
                  path: ':name',
                  name: 'artifact-detail',
                  builder: (context, state) => ArtifactViewerScreen(
                    runId: state.pathParameters['runId']!,
                    artifactName: state.pathParameters['name'],
                  ),
                ),
              ],
            ),
          ],
        ),
      ],
      errorBuilder: (context, state) => Scaffold(
        body: Center(
          child: Text('Page not found: ${state.error}'),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    // Watch auth state so router refreshes on auth changes
    ref.listen(authNotifierProvider, (_, __) {
      _router.refresh();
    });

    return MaterialApp.router(
      title: 'Orchestrator',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1565C0),
          brightness: Brightness.light,
        ),
        useMaterial3: true,
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          elevation: 0,
        ),
        cardTheme: CardThemeData(
          elevation: 2,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
      darkTheme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1565C0),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      themeMode: ThemeMode.system,
      routerConfig: _router,
    );
  }
}

