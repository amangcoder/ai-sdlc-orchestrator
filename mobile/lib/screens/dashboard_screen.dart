import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/runs_provider.dart';
import '../providers/projects_provider.dart';
import '../providers/auth_provider.dart';
import '../models/project_entry.dart';
import '../widgets/run_card.dart';
import '../widgets/error_state_widget.dart';
import '../services/api_service.dart';

/// Project-centric home screen.
///
/// Layout:
///   - AppBar with SSH quick-connect and settings buttons (REQ-022)
///   - 2-column project grid (from ~/Projects subdirectories) (REQ-001)
///   - 'Recent Runs' horizontal section (5 most-recent across all projects) (REQ-003)
///
/// Pull-to-refresh re-fetches both projects and recent runs concurrently (REQ-004).
class DashboardScreen extends ConsumerStatefulWidget {
  const DashboardScreen({super.key});

  @override
  ConsumerState<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends ConsumerState<DashboardScreen>
    with WidgetsBindingObserver {
  Timer? _pollingTimer;
  bool? _sshReachable; // null = unknown / checking

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _startPolling();
    _checkSshReachability();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _stopPolling();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.resumed:
        _startPolling();
        _checkSshReachability();
      case AppLifecycleState.inactive:
      case AppLifecycleState.paused:
      case AppLifecycleState.detached:
      case AppLifecycleState.hidden:
        _stopPolling();
    }
  }

  void _startPolling() {
    _stopPolling();
    _pollingTimer = Timer.periodic(
      const Duration(seconds: 10),
      (_) {
        ref.read(runsNotifierProvider.notifier).refresh();
        ref.read(projectsNotifierProvider.notifier).refresh();
      },
    );
  }

  void _stopPolling() {
    _pollingTimer?.cancel();
    _pollingTimer = null;
  }

  /// Pull-to-refresh: re-fetches projects and recent runs concurrently (AC-002).
  Future<void> _onRefresh() async {
    await Future.wait([
      ref.read(projectsNotifierProvider.notifier).refresh(),
      ref.read(runsNotifierProvider.notifier).refresh(),
    ]);
    _checkSshReachability();
  }

  Future<void> _checkSshReachability() async {
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null || !mounted) return;
      // Use the shared provider to avoid inline Dio construction (TASK-013).
      final api = ref.read(apiServiceProvider) ?? ApiService(credentials: creds);
      final config = await api.getSshConfig();
      if (mounted) {
        setState(() => _sshReachable = config.hostReachable);
      }
    } catch (_) {
      if (mounted) setState(() => _sshReachable = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final projectsAsync = ref.watch(projectsNotifierProvider);
    final runsAsync = ref.watch(runsNotifierProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Orchestrator'),
        actions: [
          // SSH terminal icon — only shown when host is reachable (AC-019)
          if (_sshReachable == true)
            IconButton(
              icon: const Icon(Icons.terminal),
              tooltip: 'SSH Terminal',
              onPressed: () => context.push('/ssh-terminal'),
            ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Settings',
            onPressed: () => context.push('/settings'),
          ),
          IconButton(
            icon: const Icon(Icons.tune),
            tooltip: 'Config',
            onPressed: () => context.push('/config'),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _onRefresh,
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            // ── Projects section header ───────────────────────────────────
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
              sliver: SliverToBoxAdapter(
                child: Text(
                  'Projects',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                      ),
                ),
              ),
            ),

            // ── Project grid (AC-001) ─────────────────────────────────────
            projectsAsync.when(
              loading: () => const SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: 24),
                  child: Center(child: CircularProgressIndicator()),
                ),
              ),
              error: (error, _) => SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: ErrorStateWidget.fromException(
                    error,
                    onRetry: _onRefresh,
                    onNavigate: context.go,
                  ),
                ),
              ),
              data: (projects) {
                if (projects.isEmpty) {
                  // AC-003: empty state
                  return SliverToBoxAdapter(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 24),
                      child: Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              Icons.folder_open,
                              size: 56,
                              color: Theme.of(context)
                                  .colorScheme
                                  .onSurface
                                  .withOpacity(0.3),
                            ),
                            const SizedBox(height: 12),
                            const Text(
                              'No projects found in ~/Projects',
                              textAlign: TextAlign.center,
                            ),
                            const SizedBox(height: 12),
                            TextButton.icon(
                              onPressed: _onRefresh,
                              icon: const Icon(Icons.refresh),
                              label: const Text('Refresh'),
                            ),
                          ],
                        ),
                      ),
                    ),
                  );
                }

                return SliverPadding(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  sliver: SliverGrid(
                    gridDelegate:
                        const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 2,
                      mainAxisSpacing: 8,
                      crossAxisSpacing: 8,
                      childAspectRatio: 1.3,
                    ),
                    delegate: SliverChildBuilderDelegate(
                      (context, index) {
                        final project = projects[index];
                        return _ProjectCard(
                          project: project,
                          onTap: () => context.push(
                            '/project/${project.id}',
                            extra: project.name,
                          ),
                        );
                      },
                      childCount: projects.length,
                    ),
                  ),
                );
              },
            ),

            // ── Recent Runs header ────────────────────────────────────────
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(16, 24, 16, 8),
              sliver: SliverToBoxAdapter(
                child: Text(
                  'Recent Runs',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                      ),
                ),
              ),
            ),

            // ── Recent runs horizontal list (REQ-003) ─────────────────────
            runsAsync.when(
              loading: () => const SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Center(child: CircularProgressIndicator()),
                ),
              ),
              error: (error, _) => SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: ErrorStateWidget.fromException(
                    error,
                    onRetry: _onRefresh,
                    onNavigate: context.go,
                  ),
                ),
              ),
              data: (runs) {
                // Show at most 5 most-recent runs (sorted by startTime desc).
                final recent = runs.take(5).toList();

                if (recent.isEmpty) {
                  return SliverToBoxAdapter(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'No runs yet — start your first one',
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                          const SizedBox(height: 8),
                          ElevatedButton.icon(
                            onPressed: () => context.push('/new-run'),
                            icon: const Icon(Icons.add),
                            label: const Text('New Run'),
                          ),
                        ],
                      ),
                    ),
                  );
                }

                return SliverToBoxAdapter(
                  child: SizedBox(
                    height: 160,
                    child: ListView.builder(
                      scrollDirection: Axis.horizontal,
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                      itemCount: recent.length,
                      itemBuilder: (context, index) {
                        return SizedBox(
                          width: 280,
                          child: RunCard(run: recent[index]),
                        );
                      },
                    ),
                  ),
                );
              },
            ),

            // Bottom padding so FAB doesn't overlap content
            const SliverPadding(padding: EdgeInsets.only(bottom: 80)),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => context.push('/new-run'),
        tooltip: 'Start new run',
        child: const Icon(Icons.add),
      ),
    );
  }
}

// ── Project Card Widget ───────────────────────────────────────────────────────

class _ProjectCard extends StatelessWidget {
  final ProjectEntry project;
  final VoidCallback onTap;

  const _ProjectCard({
    required this.project,
    required this.onTap,
  });

  /// Returns a human-readable relative time string (e.g. '2h ago').
  String _relativeTime(DateTime dt) {
    final diff = DateTime.now().difference(dt);
    if (diff.inMinutes < 1) return 'just now';
    if (diff.inHours < 1) return '${diff.inMinutes}m ago';
    if (diff.inDays < 1) return '${diff.inHours}h ago';
    if (diff.inDays < 30) return '${diff.inDays}d ago';
    final months = (diff.inDays / 30).floor();
    return '${months}mo ago';
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Project ${project.name}, ${project.runCount} runs',
      button: true,
      child: Card(
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.folder, size: 18),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        project.name,
                        style: Theme.of(context)
                            .textTheme
                            .titleSmall
                            ?.copyWith(fontWeight: FontWeight.bold),
                        overflow: TextOverflow.ellipsis,
                        maxLines: 1,
                      ),
                    ),
                  ],
                ),
                const Spacer(),
                Text(
                  _relativeTime(project.lastModified),
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context)
                            .colorScheme
                            .onSurface
                            .withOpacity(0.6),
                      ),
                ),
                const SizedBox(height: 4),
                // Run-count badge
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.primaryContainer,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(
                    '${project.runCount} run${project.runCount == 1 ? '' : 's'}',
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: Theme.of(context)
                              .colorScheme
                              .onPrimaryContainer,
                          fontWeight: FontWeight.w600,
                        ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
