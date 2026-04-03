import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/artifact_search_provider.dart';
import '../widgets/error_state_widget.dart';

/// Global artifact search screen with debounced input.
///
/// REQ-010: Fetches GET /api/v1/artifacts/search and renders:
///   - Search TextField at the top with 400ms debounce
///   - Results list: artifact name as title, run/agent as subtitle
///   - Tapping a result navigates to the artifact detail view
///   - Empty state when no results are found
class GlobalArtifactSearchScreen extends ConsumerStatefulWidget {
  const GlobalArtifactSearchScreen({super.key});

  @override
  ConsumerState<GlobalArtifactSearchScreen> createState() =>
      _GlobalArtifactSearchScreenState();
}

class _GlobalArtifactSearchScreenState
    extends ConsumerState<GlobalArtifactSearchScreen> {
  final TextEditingController _searchController = TextEditingController();
  Timer? _debounce;

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  void _onSearchChanged(String query) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () {
      if (!mounted) return;
      ref.read(artifactSearchNotifierProvider.notifier).search(query);
    });
  }

  void _clearSearch() {
    _searchController.clear();
    _debounce?.cancel();
    ref.read(artifactSearchNotifierProvider.notifier).clear();
  }

  @override
  Widget build(BuildContext context) {
    final searchAsync = ref.watch(artifactSearchNotifierProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Artifact Search'),
      ),
      body: Column(
        children: [
          // ── Search bar ────────────────────────────────────────────────
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            child: Semantics(
              label: 'Search artifacts',
              child: TextField(
                controller: _searchController,
                autofocus: true,
                textInputAction: TextInputAction.search,
                decoration: InputDecoration(
                  hintText: 'Search artifacts by name, schema, or agent…',
                  prefixIcon: const Icon(Icons.search),
                  suffixIcon: _searchController.text.isNotEmpty
                      ? IconButton(
                          icon: const Icon(Icons.clear),
                          tooltip: 'Clear',
                          onPressed: _clearSearch,
                        )
                      : null,
                  border: const OutlineInputBorder(),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surfaceContainerHighest,
                ),
                onChanged: _onSearchChanged,
              ),
            ),
          ),

          // ── Results ───────────────────────────────────────────────────
          Expanded(
            child: searchAsync.when(
              loading: () =>
                  const Center(child: CircularProgressIndicator()),
              error: (error, _) => ErrorStateWidget.fromException(error),
              data: (searchState) {
                if (searchState.query.isEmpty) {
                  return const Center(
                    child: Padding(
                      padding: EdgeInsets.all(32),
                      child: Text(
                        'Type to search artifacts across all runs.',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  );
                }
                if (searchState.results.isEmpty) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.search_off,
                            size: 48,
                            color: Theme.of(context)
                                .colorScheme
                                .onSurface
                                .withOpacity(0.3),
                          ),
                          const SizedBox(height: 12),
                          Text(
                            'No artifacts found for "${searchState.query}".',
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                    ),
                  );
                }

                return ListView.separated(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  itemCount: searchState.results.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (context, index) {
                    final result = searchState.results[index];
                    final shortRunId = result.runId.length >= 8
                        ? result.runId.substring(0, 8)
                        : result.runId;

                    return Semantics(
                      label:
                          'Artifact ${result.artifactName} from run $shortRunId by agent ${result.agent}',
                      button: true,
                      child: ListTile(
                        leading: const Icon(Icons.description_outlined),
                        title: Text(result.artifactName),
                        subtitle: Text(
                          'Run: $shortRunId • Agent: ${result.agent}',
                          overflow: TextOverflow.ellipsis,
                        ),
                        trailing: result.schema.isNotEmpty
                            ? Chip(
                                label: Text(
                                  result.schema,
                                  style: Theme.of(context)
                                      .textTheme
                                      .labelSmall,
                                ),
                                padding: EdgeInsets.zero,
                              )
                            : null,
                        onTap: () => context.push(
                          '/runs/${result.runId}/artifacts/${result.artifactName}',
                        ),
                      ),
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
