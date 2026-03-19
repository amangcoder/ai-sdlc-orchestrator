import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/run_summary.dart';
import '../services/api_service.dart';
import 'auth_provider.dart';

/// Async notifier that manages the list of orchestration runs.
class RunsNotifier extends AsyncNotifier<List<RunSummary>> {
  @override
  Future<List<RunSummary>> build() async {
    return _fetchRuns();
  }

  Future<List<RunSummary>> _fetchRuns() async {
    final creds = await ref.read(authNotifierProvider.future);
    if (creds == null) return [];
    // Use the shared provider — avoids constructing a new Dio instance inline.
    final api = ref.read(apiServiceProvider) ?? ApiService(credentials: creds);
    try {
      final runs = await api.listRuns();
      runs.sort((a, b) {
        if (a.startTime == null && b.startTime == null) return 0;
        if (a.startTime == null) return 1;
        if (b.startTime == null) return -1;
        return b.startTime!.compareTo(a.startTime!);
      });
      return runs;
    } on AuthException {
      // 401 response — clear credentials to trigger GoRouter redirect to
      // /settings automatically (TASK-019: AC requires 401 → authProvider
      // clear → GoRouter redirect without user interaction).
      await ref.read(authNotifierProvider.notifier).clear();
      return [];
    }
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetchRuns);
  }
}

/// Global provider for the list of runs.
final runsNotifierProvider =
    AsyncNotifierProvider<RunsNotifier, List<RunSummary>>(
        () => RunsNotifier());
