import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/runs_provider.dart';
import '../providers/auth_provider.dart';
import '../widgets/run_card.dart';
import '../widgets/error_state_widget.dart';

/// Main dashboard showing active and historical orchestration runs.
/// Polls every 10 seconds when the screen is foregrounded.
class DashboardScreen extends ConsumerStatefulWidget {
  const DashboardScreen({super.key});

  @override
  ConsumerState<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends ConsumerState<DashboardScreen>
    with WidgetsBindingObserver {
  Timer? _pollingTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _startPolling();
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
      (_) => ref.read(runsNotifierProvider.notifier).refresh(),
    );
  }

  void _stopPolling() {
    _pollingTimer?.cancel();
    _pollingTimer = null;
  }

  Future<void> _onRefresh() async {
    await ref.read(runsNotifierProvider.notifier).refresh();
  }

  @override
  Widget build(BuildContext context) {
    final runsAsync = ref.watch(runsNotifierProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Orchestrator'),
        actions: [
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
        child: runsAsync.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, stack) => ErrorStateWidget.fromException(
            error,
            onRetry: _onRefresh,
            onNavigate: context.go,
          ),
          data: (runs) {
            if (runs.isEmpty) {
              return CustomScrollView(
                // Makes pull-to-refresh work on empty state
                physics: const AlwaysScrollableScrollPhysics(),
                slivers: [
                  SliverFillRemaining(
                    child: Center(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.rocket_launch_outlined,
                            size: 64,
                            color: Theme.of(context)
                                .colorScheme
                                .onSurface
                                .withOpacity(0.3),
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'No runs yet — start your first one',
                            style:
                                Theme.of(context).textTheme.bodyLarge,
                          ),
                          const SizedBox(height: 24),
                          ElevatedButton.icon(
                            onPressed: () => context.push('/new-run'),
                            icon: const Icon(Icons.add),
                            label: const Text('New Run'),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              );
            }

            return ListView.builder(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: runs.length,
              itemBuilder: (context, index) {
                return RunCard(run: runs[index]);
              },
            );
          },
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
