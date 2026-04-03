import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/artifact_search_result_model.dart';
import '../services/api_service.dart';
import 'auth_provider.dart';

/// State held by [ArtifactSearchNotifier].
class ArtifactSearchState {
  final String query;
  final List<ArtifactSearchResult> results;

  const ArtifactSearchState({
    this.query = '',
    this.results = const [],
  });

  ArtifactSearchState copyWith({
    String? query,
    List<ArtifactSearchResult>? results,
  }) {
    return ArtifactSearchState(
      query: query ?? this.query,
      results: results ?? this.results,
    );
  }
}

/// Async notifier that manages artifact search query state and results.
class ArtifactSearchNotifier
    extends AsyncNotifier<ArtifactSearchState> {
  @override
  Future<ArtifactSearchState> build() async {
    return const ArtifactSearchState();
  }

  /// Searches artifacts with [query]. Clears results when query is empty.
  Future<void> search(String query, {String? type, String? agent}) async {
    if (query.trim().isEmpty) {
      state = AsyncData(const ArtifactSearchState());
      return;
    }

    state = AsyncData(state.valueOrNull?.copyWith(query: query) ??
        ArtifactSearchState(query: query));

    final creds = await ref.read(authNotifierProvider.future);
    if (creds == null) {
      state = AsyncData(ArtifactSearchState(query: query));
      return;
    }
    final api = ref.read(apiServiceProvider) ?? ApiService(credentials: creds);
    try {
      final results = await api.searchArtifacts(query, type: type, agent: agent);
      if (state.valueOrNull?.query == query) {
        state = AsyncData(ArtifactSearchState(query: query, results: results));
      }
    } on AuthException {
      await ref.read(authNotifierProvider.notifier).clear();
    } catch (e, st) {
      state = AsyncError(e, st);
    }
  }

  /// Clears the current search results and query.
  void clear() {
    state = const AsyncData(ArtifactSearchState());
  }
}

/// Global provider for artifact search.
final artifactSearchNotifierProvider =
    AsyncNotifierProvider<ArtifactSearchNotifier, ArtifactSearchState>(
        () => ArtifactSearchNotifier());
