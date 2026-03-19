import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../models/run_summary.dart';
import '../providers/auth_provider.dart';
import '../services/api_service.dart';
import '../widgets/run_card.dart';
import '../widgets/error_state_widget.dart';

/// Project-scoped view showing all runs for a specific project directory.
///
/// Features:
///   - Status filter tabs (All / Running / Completed / Failed / Cancelled)
///   - Full-text search by featureRequest field with ~300ms debounce
///   - Empty state with Start Run button pre-filling the projectId
///   - Pull-to-refresh
///
/// Client-side filtering is applied as an AND predicate of the active status
/// tab and the search query — no additional round-trips are needed.
class ProjectDetailScreen extends ConsumerStatefulWidget {
  final String projectId;
  final String projectName;

  const ProjectDetailScreen({
    super.key,
    required this.projectId,
    required this.projectName,
  });

  @override
  ConsumerState<ProjectDetailScreen> createState() =>
      _ProjectDetailScreenState();
}

class _ProjectDetailScreenState
    extends ConsumerState<ProjectDetailScreen> {
  List<RunSummary> _allRuns = [];
  bool _isLoading = true;
  Object? _error;

  String _activeStatusFilter = 'All';
  String _searchQuery = '';

  Timer? _searchDebounce;
  final TextEditingController _searchController = TextEditingController();

  static const List<String> _statusFilters = [
    'All',
    'Running',
    'Completed',
    'Failed',
    'Cancelled',
  ];

  @override
  void initState() {
    super.initState();
    _loadRuns();
  }

  @override
  void dispose() {
    // Reset filter to 'All' on navigate-away (REQ-016).
    _activeStatusFilter = 'All';
    _searchDebounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _loadRuns() async {
    if (mounted) setState(() { _isLoading = true; _error = null; });
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) {
        if (mounted) context.go('/settings');
        return;
      }
      final api = ref.read(apiServiceProvider) ??
          ApiService(credentials: creds);
      final runs = await api.listRunsByProject(widget.projectId);
      // Sort by start time descending.
      runs.sort((a, b) {
        if (a.startTime == null && b.startTime == null) return 0;
        if (a.startTime == null) return 1;
        if (b.startTime == null) return -1;
        return b.startTime!.compareTo(a.startTime!);
      });
      if (mounted) {
        setState(() {
          _allRuns = runs;
          _isLoading = false;
        });
      }
    } on AuthException {
      await ref.read(authNotifierProvider.notifier).clear();
      if (mounted) context.go('/settings');
    } catch (e) {
      if (mounted) setState(() { _error = e; _isLoading = false; });
    }
  }

  // ── Filtering ─────────────────────────────────────────────────────────────

  List<RunSummary> get _filteredRuns =>
      _applyFilters(_allRuns, _activeStatusFilter, _searchQuery);

  /// Applies status and search predicates simultaneously (AND logic).
  List<RunSummary> _applyFilters(
    List<RunSummary> runs,
    String statusFilter,
    String query,
  ) {
    return runs.where((run) {
      // Status predicate
      final matchesStatus = statusFilter == 'All' ||
          run.status.toLowerCase() == statusFilter.toLowerCase();

      // Search predicate (case-insensitive substring match on featureRequest)
      final matchesSearch = query.isEmpty ||
          run.featureRequest.toLowerCase().contains(query.toLowerCase());

      return matchesStatus && matchesSearch;
    }).toList();
  }

  void _onSearchChanged(String value) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 300), () {
      if (mounted) setState(() => _searchQuery = value);
    });
  }

  // ─────────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.projectName),
      ),
      body: Column(
        children: [
          // ── Search bar ─────────────────────────────────────────────────
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
            child: TextField(
              controller: _searchController,
              decoration: InputDecoration(
                hintText: 'Search by feature request…',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: _searchQuery.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear),
                        onPressed: () {
                          _searchController.clear();
                          setState(() => _searchQuery = '');
                        },
                      )
                    : null,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
                isDense: true,
              ),
              onChanged: _onSearchChanged,
            ),
          ),

          // ── Status filter tabs ─────────────────────────────────────────
          SizedBox(
            height: 48,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding:
                  const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              children: _statusFilters.map((filter) {
                final isActive = _activeStatusFilter == filter;
                return Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: ChoiceChip(
                    label: Text(filter),
                    selected: isActive,
                    onSelected: (_) =>
                        setState(() => _activeStatusFilter = filter),
                  ),
                );
              }).toList(),
            ),
          ),

          // ── Runs list ──────────────────────────────────────────────────
          Expanded(
            child: _buildBody(),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        // Pass workspaceId so new runs are always created in this project's
        // folder — core workspace-isolation requirement (feature request).
        onPressed: () => context.push(
          '/new-run',
          extra: <String, String>{'workspaceId': widget.projectId},
        ),
        tooltip: 'Start new run',
        child: const Icon(Icons.add),
      ),
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_error != null) {
      return ErrorStateWidget.fromException(
        _error!,
        onRetry: _loadRuns,
        onNavigate: context.go,
      );
    }

    final filtered = _filteredRuns;

    if (filtered.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.rocket_launch_outlined,
                size: 56,
                color:
                    Theme.of(context).colorScheme.onSurface.withOpacity(0.3),
              ),
              const SizedBox(height: 16),
              Text(
                _allRuns.isEmpty
                    ? 'No runs for this project yet'
                    : 'No runs match the current filter',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyLarge,
              ),
              if (_allRuns.isEmpty) ...[
                const SizedBox(height: 20),
                ElevatedButton.icon(
                  onPressed: () => context.push(
                    '/new-run',
                    extra: <String, String>{'workspaceId': widget.projectId},
                  ),
                  icon: const Icon(Icons.add),
                  label: const Text('Start Run'),
                ),
              ],
            ],
          ),
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _loadRuns,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: filtered.length,
        itemBuilder: (context, index) => RunCard(run: filtered[index]),
      ),
    );
  }
}
