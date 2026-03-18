import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/auth_provider.dart';
import '../services/api_service.dart';
import '../widgets/json_tree_node.dart';
import '../widgets/error_state_widget.dart';

/// Shows either a list of available artifacts or a JSON tree for a single artifact.
///
/// Uses ListView.builder for root-level keys to ensure O(1) rendering of large
/// artifacts (REQ-033). Search jumps to the first root-level key matching the query.
class ArtifactViewerScreen extends ConsumerStatefulWidget {
  final String runId;
  final String? artifactName;

  const ArtifactViewerScreen({
    super.key,
    required this.runId,
    this.artifactName,
  });

  @override
  ConsumerState<ArtifactViewerScreen> createState() =>
      _ArtifactViewerScreenState();
}

class _ArtifactViewerScreenState extends ConsumerState<ArtifactViewerScreen> {
  List<String>? _artifactList;
  Map<String, dynamic>? _artifactData;
  bool _isLoading = true;
  Object? _error;
  final TextEditingController _searchController = TextEditingController();
  Timer? _searchDebounce;

  /// GlobalKey map for root-level artifact keys — used for search scrolling.
  final Map<String, GlobalKey> _rootKeyMap = {};

  /// ScrollController for the ListView.builder in detail view.
  final ScrollController _scrollController = ScrollController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _searchController.dispose();
    _searchDebounce?.cancel();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) {
        if (mounted) context.go('/settings');
        return;
      }
      final api = ApiService(credentials: creds);
      if (widget.artifactName == null) {
        final list = await api.listArtifacts(widget.runId);
        list.sort();
        if (mounted) {
          setState(() {
            _artifactList = list;
            _isLoading = false;
          });
        }
      } else {
        final data = await api.getArtifact(widget.runId, widget.artifactName!);
        if (mounted) {
          // Build stable GlobalKey map for root-level keys (created once on load)
          _rootKeyMap.clear();
          for (final key in data.keys) {
            _rootKeyMap[key] = GlobalKey(debugLabel: 'artifact_$key');
          }
          setState(() {
            _artifactData = data;
            _isLoading = false;
          });
        }
      }
    } on AuthException {
      await ref.read(authNotifierProvider.notifier).clear();
      if (mounted) context.go('/settings');
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e;
          _isLoading = false;
        });
      }
    }
  }

  void _onSearchChanged(String query) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 300), () {
      if (!mounted) return;
      setState(() {}); // Rebuild to highlight matched key
      _scrollToFirstMatch(query);
    });
  }

  /// Scroll to the first root-level key matching [query] (case-insensitive
  /// substring match). Uses Scrollable.ensureVisible for accurate positioning.
  void _scrollToFirstMatch(String query) {
    if (query.isEmpty) return;
    final lowerQuery = query.toLowerCase();
    for (final entry in (_artifactData ?? {}).entries) {
      if (entry.key.toLowerCase().contains(lowerQuery)) {
        final nodeKey = _rootKeyMap[entry.key];
        if (nodeKey == null) return;
        final ctx = nodeKey.currentContext;
        if (ctx != null) {
          Scrollable.ensureVisible(
            ctx,
            duration: const Duration(milliseconds: 300),
            curve: Curves.easeOut,
          );
        }
        return;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.artifactName ?? 'Artifacts';

    if (_isLoading) {
      return Scaffold(
        appBar: AppBar(title: Text(title)),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    if (_error != null) {
      return Scaffold(
        appBar: AppBar(title: Text(title)),
        body: ErrorStateWidget.fromException(
          _error!,
          onRetry: _load,
          onNavigate: context.go,
        ),
      );
    }

    if (widget.artifactName == null) {
      return _buildArtifactList(context);
    } else {
      return _buildArtifactDetail(context);
    }
  }

  Widget _buildArtifactList(BuildContext context) {
    final list = _artifactList ?? [];
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.runId.length >= 8
              ? '${widget.runId.substring(0, 8)} — Artifacts'
              : 'Artifacts',
        ),
      ),
      body: list.isEmpty
          ? const Center(child: Text('No artifacts available'))
          : ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: list.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final name = list[index];
                return ListTile(
                  leading: const Icon(Icons.description_outlined),
                  title: Text(name),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => context.push(
                    '/runs/${widget.runId}/artifacts/$name',
                  ),
                );
              },
            ),
    );
  }

  Widget _buildArtifactDetail(BuildContext context) {
    final data = _artifactData ?? {};
    final entries = data.entries.toList();
    final searchQuery = _searchController.text.toLowerCase();

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.artifactName!),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(56),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: TextField(
              controller: _searchController,
              decoration: InputDecoration(
                hintText: 'Search keys...',
                isDense: true,
                border: const OutlineInputBorder(),
                prefixIcon: const Icon(Icons.search, size: 18),
                suffixIcon: _searchController.text.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear, size: 18),
                        onPressed: () {
                          _searchController.clear();
                          _onSearchChanged('');
                        },
                      )
                    : null,
                filled: true,
                fillColor: Theme.of(context).colorScheme.surface,
              ),
              onChanged: _onSearchChanged,
            ),
          ),
        ),
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        // ListView.builder renders only visible root-level items, giving O(1)
        // complexity for large artifacts with many root-level keys (REQ-033).
        child: ListView.builder(
          controller: _scrollController,
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(12),
          itemCount: entries.length,
          itemBuilder: (context, index) {
            final entry = entries[index];
            final isMatch = searchQuery.isNotEmpty &&
                entry.key.toLowerCase().contains(searchQuery);
            return Container(
              key: _rootKeyMap[entry.key],
              decoration: isMatch
                  ? BoxDecoration(
                      color: Theme.of(context)
                          .colorScheme
                          .primaryContainer
                          .withOpacity(0.3),
                      borderRadius: BorderRadius.circular(4),
                    )
                  : null,
              child: JsonTreeNode(
                label: entry.key,
                value: entry.value,
                depth: 0,
                // Collapse by default when there are many top-level keys to
                // avoid excessive initial render work.
                initiallyExpanded: entries.length < 20,
              ),
            );
          },
        ),
      ),
    );
  }
}
