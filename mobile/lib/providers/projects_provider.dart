import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/project_entry.dart';
import '../services/api_service.dart';
import 'auth_provider.dart';

/// Async notifier that manages the list of project directories.
///
/// Follows the same structural pattern as [RunsNotifier] in runs_provider.dart.
class ProjectsNotifier extends AsyncNotifier<List<ProjectEntry>> {
  @override
  Future<List<ProjectEntry>> build() async {
    return _fetchProjects();
  }

  Future<List<ProjectEntry>> _fetchProjects() async {
    final creds = await ref.read(authNotifierProvider.future);
    if (creds == null) return [];

    // Use the shared provider — avoids constructing a new Dio instance inline.
    final api = ref.read(apiServiceProvider);
    if (api == null) return [];

    try {
      final projects = await api.listProjects();
      // Sort by most-recently-modified first.
      projects.sort((a, b) => b.lastModified.compareTo(a.lastModified));
      return projects;
    } on AuthException {
      // 401 → clear stale credentials and redirect via GoRouter.
      await ref.read(authNotifierProvider.notifier).clear();
      return [];
    } on NotFoundException {
      // 404 → endpoint not available or projects_root not configured.
      return [];
    }
  }

  /// Re-fetches projects, transitioning state through AsyncLoading first.
  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetchProjects);
  }
}

/// Global provider for the list of project directories.
final projectsNotifierProvider =
    AsyncNotifierProvider<ProjectsNotifier, List<ProjectEntry>>(
        () => ProjectsNotifier());
