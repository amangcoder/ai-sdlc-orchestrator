import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/directory_entry.dart';
import '../services/api_service.dart';
import 'auth_provider.dart';

/// Immutable state for the directory browser navigation.
class DirectoryBrowserState {
  /// Breadcrumb stack: first element is root, last element is current level.
  /// Empty means the browser is at the projects_root level.
  final List<DirectoryEntry> breadcrumbs;

  /// Immediate children of the current directory level.
  final List<DirectoryEntry> children;

  /// True when the API reports the server-side max_browse_depth has been hit.
  final bool atDepthLimit;

  const DirectoryBrowserState({
    this.breadcrumbs = const [],
    this.children = const [],
    this.atDepthLimit = false,
  });

  DirectoryBrowserState copyWith({
    List<DirectoryEntry>? breadcrumbs,
    List<DirectoryEntry>? children,
    bool? atDepthLimit,
  }) =>
      DirectoryBrowserState(
        breadcrumbs: breadcrumbs ?? this.breadcrumbs,
        children: children ?? this.children,
        atDepthLimit: atDepthLimit ?? this.atDepthLimit,
      );
}

/// Riverpod AsyncNotifier managing the directory navigation stack.
///
/// Follows the same AsyncNotifier pattern as [RunsNotifier] and [AuthNotifier]:
/// state is wrapped in AsyncValue automatically; errors surface through
/// AsyncValue.error (never swallowed into a String field).
class DirectoryBrowserNotifier
    extends AsyncNotifier<DirectoryBrowserState> {
  @override
  Future<DirectoryBrowserState> build() async {
    return const DirectoryBrowserState();
  }

  ApiService? _buildApiService(dynamic creds) {
    if (creds == null) return null;
    return ApiService(credentials: creds as dynamic);
  }

  /// Loads the root entry and its immediate children.
  ///
  /// Call this from [initState] of the directory browser widget.
  Future<void> loadRoot() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) {
        throw const AuthException('Not authenticated');
      }
      final api = ApiService(credentials: creds);
      final root = await api.getDirectoryRoot();
      final childrenResponse = await api.getDirectoryChildren(root.id);
      return DirectoryBrowserState(
        breadcrumbs: const [],
        children: childrenResponse.entries,
        atDepthLimit: childrenResponse.atDepthLimit,
      );
    });
  }

  /// Navigates into [entry], appending it to the breadcrumb trail.
  Future<void> navigateInto(DirectoryEntry entry) async {
    final current = state.valueOrNull;
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) throw const AuthException('Not authenticated');
      final api = ApiService(credentials: creds);
      final childrenResponse = await api.getDirectoryChildren(entry.id);
      final newBreadcrumbs = <DirectoryEntry>[
        ...(current?.breadcrumbs ?? []),
        entry,
      ];
      return DirectoryBrowserState(
        breadcrumbs: newBreadcrumbs,
        children: childrenResponse.entries,
        atDepthLimit: childrenResponse.atDepthLimit,
      );
    });
  }

  /// Navigates back to the parent directory.
  ///
  /// If the breadcrumb stack is empty we are already at root — does nothing.
  Future<void> navigateUp() async {
    final current = state.valueOrNull;
    if (current == null || current.breadcrumbs.isEmpty) return;

    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) throw const AuthException('Not authenticated');
      final api = ApiService(credentials: creds);

      final newBreadcrumbs =
          current.breadcrumbs.sublist(0, current.breadcrumbs.length - 1);

      if (newBreadcrumbs.isEmpty) {
        // Back to root — reload root children
        final root = await api.getDirectoryRoot();
        final childrenResponse = await api.getDirectoryChildren(root.id);
        return DirectoryBrowserState(
          breadcrumbs: const [],
          children: childrenResponse.entries,
          atDepthLimit: childrenResponse.atDepthLimit,
        );
      } else {
        // Back to parent breadcrumb entry
        final parent = newBreadcrumbs.last;
        final childrenResponse = await api.getDirectoryChildren(parent.id);
        return DirectoryBrowserState(
          breadcrumbs: newBreadcrumbs,
          children: childrenResponse.entries,
          atDepthLimit: childrenResponse.atDepthLimit,
        );
      }
    });
  }

  /// Creates a new folder named [name] in the current directory level.
  ///
  /// Refreshes the children list on success. Errors surface through
  /// AsyncValue.error so the widget can display them inline.
  Future<void> createFolder(String name) async {
    final current = state.valueOrNull;
    // Determine the parent dir ID
    final String parentId;
    if (current == null || current.breadcrumbs.isEmpty) {
      // We are at root — we need the root entry id
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) throw const AuthException('Not authenticated');
      final api = ApiService(credentials: creds);
      final root = await api.getDirectoryRoot();
      parentId = root.id;
    } else {
      parentId = current.breadcrumbs.last.id;
    }

    state = await AsyncValue.guard(() async {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) throw const AuthException('Not authenticated');
      final api = ApiService(credentials: creds);
      await api.createDirectory(parentId, name);
      // Reload children after creation
      final childrenResponse = await api.getDirectoryChildren(parentId);
      return (current ?? const DirectoryBrowserState()).copyWith(
        children: childrenResponse.entries,
        atDepthLimit: childrenResponse.atDepthLimit,
      );
    });
  }

  /// Reloads children of the current directory level.
  Future<void> refresh() async {
    final current = state.valueOrNull;
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) throw const AuthException('Not authenticated');
      final api = ApiService(credentials: creds);

      if (current == null || current.breadcrumbs.isEmpty) {
        final root = await api.getDirectoryRoot();
        final childrenResponse = await api.getDirectoryChildren(root.id);
        return DirectoryBrowserState(
          breadcrumbs: const [],
          children: childrenResponse.entries,
          atDepthLimit: childrenResponse.atDepthLimit,
        );
      } else {
        final parent = current.breadcrumbs.last;
        final childrenResponse = await api.getDirectoryChildren(parent.id);
        return DirectoryBrowserState(
          breadcrumbs: current.breadcrumbs,
          children: childrenResponse.entries,
          atDepthLimit: childrenResponse.atDepthLimit,
        );
      }
    });
  }

  /// Navigates directly to a breadcrumb level by index.
  Future<void> navigateToBreadcrumb(int index) async {
    final current = state.valueOrNull;
    if (current == null) return;
    if (index < 0) {
      // Navigate to root
      await loadRoot();
      return;
    }
    if (index >= current.breadcrumbs.length) return;
    final targetEntry = current.breadcrumbs[index];
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) throw const AuthException('Not authenticated');
      final api = ApiService(credentials: creds);
      final childrenResponse =
          await api.getDirectoryChildren(targetEntry.id);
      return DirectoryBrowserState(
        breadcrumbs: current.breadcrumbs.sublist(0, index + 1),
        children: childrenResponse.entries,
        atDepthLimit: childrenResponse.atDepthLimit,
      );
    });
  }
}

/// Global provider for directory browser state.
final directoryBrowserProvider =
    AsyncNotifierProvider<DirectoryBrowserNotifier, DirectoryBrowserState>(
        () => DirectoryBrowserNotifier());
