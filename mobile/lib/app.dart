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
import 'screens/ssh_terminal_screen.dart';
import 'screens/project_detail_screen.dart';
import 'screens/cost_analytics_screen.dart';
import 'screens/slo_compliance_screen.dart';
import 'screens/alerts_screen.dart';
import 'screens/artifact_search_screen.dart';

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
          builder: (context, state) {
            // workspaceId may be passed as GoRouter 'extra' from
            // ProjectDetailScreen's "Start Run" button so the new run is
            // pre-scoped to the project folder (core feature requirement).
            final extra = state.extra;
            String? workspaceId;
            if (extra is Map<String, String>) {
              workspaceId = extra['workspaceId'];
            } else if (extra is Map<String, dynamic>) {
              workspaceId = extra['workspaceId'] as String?;
            }
            return NewRunScreen(preselectedWorkspaceId: workspaceId);
          },
        ),
        GoRoute(
          path: '/config',
          name: 'config',
          builder: (context, state) => const ConfigEditorScreen(),
        ),
        GoRoute(
          path: '/ssh-terminal',
          name: 'ssh-terminal',
          builder: (context, state) => const SshTerminalScreen(),
        ),
        // Project detail — navigated from a project card on DashboardScreen.
        // projectName is passed as GoRouter 'extra' from context.push().
        GoRoute(
          path: '/project/:projectId',
          name: 'project-detail',
          builder: (context, state) => ProjectDetailScreen(
            projectId: state.pathParameters['projectId']!,
            projectName: state.extra as String? ??
                state.uri.queryParameters['name'] ??
                '',
          ),
        ),
        GoRoute(
          path: '/cost-analytics',
          name: 'cost-analytics',
          builder: (context, state) => const CostAnalyticsScreen(),
        ),
        GoRoute(
          path: '/slo',
          name: 'slo',
          builder: (context, state) => const SLOComplianceScreen(),
        ),
        GoRoute(
          path: '/alerts',
          name: 'alerts',
          builder: (context, state) => const AlertsScreen(),
        ),
        GoRoute(
          path: '/artifact-search',
          name: 'artifact-search',
          builder: (context, state) => const GlobalArtifactSearchScreen(),
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
