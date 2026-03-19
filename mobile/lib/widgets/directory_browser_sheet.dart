import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/directory_entry.dart';
import '../providers/directory_browser_provider.dart';
import '../services/api_service.dart';

/// A navigable bottom-sheet directory picker backed by the dynamic directory
/// browse API.
///
/// Calls [DirectoryBrowserNotifier.loadRoot] on creation.  When the user taps
/// the "Select" button on an entry, the sheet pops with that [DirectoryEntry]
/// as its result so the caller can use `await showModalBottomSheet(...)`.
///
/// Example:
/// ```dart
/// final entry = await showModalBottomSheet<DirectoryEntry>(
///   context: context,
///   isScrollControlled: true,
///   builder: (_) => const DirectoryBrowserSheet(),
/// );
/// if (entry != null) { /* use entry.id as workspace_id */ }
/// ```
class DirectoryBrowserSheet extends ConsumerStatefulWidget {
  const DirectoryBrowserSheet({super.key});

  @override
  ConsumerState<DirectoryBrowserSheet> createState() =>
      _DirectoryBrowserSheetState();
}

class _DirectoryBrowserSheetState
    extends ConsumerState<DirectoryBrowserSheet> {
  final _newFolderController = TextEditingController();
  String? _newFolderError;
  bool _isCreatingFolder = false;

  @override
  void initState() {
    super.initState();
    // Load root on first build using a post-frame callback so we don't call
    // the notifier during the build phase.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(directoryBrowserProvider.notifier).loadRoot();
    });
  }

  @override
  void dispose() {
    _newFolderController.dispose();
    super.dispose();
  }

  Future<void> _showNewFolderDialog(bool atDepthLimit) async {
    if (atDepthLimit) return;
    _newFolderController.clear();
    setState(() => _newFolderError = null);

    await showDialog<void>(
      context: context,
      builder: (dialogCtx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: const Text('New Folder'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                controller: _newFolderController,
                autofocus: true,
                decoration: InputDecoration(
                  labelText: 'Folder name',
                  border: const OutlineInputBorder(),
                  errorText: _newFolderError,
                ),
                onChanged: (_) =>
                    setDialogState(() => _newFolderError = null),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogCtx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: _isCreatingFolder
                  ? null
                  : () async {
                      final name = _newFolderController.text.trim();
                      if (name.isEmpty) {
                        setDialogState(() =>
                            _newFolderError = 'Folder name is required');
                        return;
                      }
                      setDialogState(() => _isCreatingFolder = true);
                      try {
                        await ref
                            .read(directoryBrowserProvider.notifier)
                            .createFolder(name);
                        if (dialogCtx.mounted) Navigator.pop(dialogCtx);
                      } on ConflictException {
                        setDialogState(() {
                          _newFolderError =
                              'A folder with that name already exists';
                          _isCreatingFolder = false;
                        });
                      } on ServerException catch (e) {
                        setDialogState(() {
                          _newFolderError =
                              e.message.contains('name')
                                  ? 'Invalid folder name'
                                  : e.message;
                          _isCreatingFolder = false;
                        });
                      } catch (e) {
                        setDialogState(() {
                          _newFolderError = 'Failed to create folder: $e';
                          _isCreatingFolder = false;
                        });
                      }
                    },
              child: _isCreatingFolder
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Create'),
            ),
          ],
        ),
      ),
    );
    setState(() => _isCreatingFolder = false);
  }

  @override
  Widget build(BuildContext context) {
    final browserAsync = ref.watch(directoryBrowserProvider);
    final theme = Theme.of(context);

    return DraggableScrollableSheet(
      initialChildSize: 0.75,
      minChildSize: 0.5,
      maxChildSize: 0.95,
      expand: false,
      builder: (sheetCtx, scrollController) => Scaffold(
        // Use a Scaffold inside the sheet for AppBar + FAB support
        backgroundColor: theme.colorScheme.surface,
        appBar: AppBar(
          backgroundColor: theme.colorScheme.surface,
          elevation: 0,
          automaticallyImplyLeading: false,
          title: const Text('Select Directory'),
          leading: browserAsync.whenOrNull(
                data: (state) => state.breadcrumbs.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.arrow_back),
                        tooltip: 'Go up',
                        onPressed: () => ref
                            .read(directoryBrowserProvider.notifier)
                            .navigateUp(),
                      )
                    : null,
              ) ??
              null,
          actions: [
            IconButton(
              icon: const Icon(Icons.close),
              tooltip: 'Cancel',
              onPressed: () => Navigator.pop(context),
            ),
          ],
        ),
        body: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // ── Breadcrumb bar ──────────────────────────────────────
            browserAsync.whenOrNull(
                  data: (state) => _buildBreadcrumbs(state, theme),
                ) ??
                const SizedBox.shrink(),

            // ── Main content ────────────────────────────────────────
            Expanded(
              child: browserAsync.when(
                loading: () => const Center(
                  child: CircularProgressIndicator(
                      semanticsLabel: 'Loading directories'),
                ),
                error: (error, _) => _buildErrorBanner(error, theme),
                data: (state) => _buildDirectoryList(
                    state, scrollController, theme),
              ),
            ),
          ],
        ),
        floatingActionButton: browserAsync.whenOrNull(
          data: (state) => state.atDepthLimit
              ? null
              : FloatingActionButton.extended(
                  onPressed: () => _showNewFolderDialog(state.atDepthLimit),
                  icon: const Icon(Icons.create_new_folder),
                  label: const Text('New Folder'),
                  tooltip: 'Create new folder',
                ),
        ),
      ),
    );
  }

  Widget _buildBreadcrumbs(
      DirectoryBrowserState state, ThemeData theme) {
    if (state.breadcrumbs.isEmpty) return const SizedBox.shrink();

    return Container(
      height: 40,
      color: theme.colorScheme.surfaceContainerHighest.withOpacity(0.5),
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 8),
        itemCount: state.breadcrumbs.length + 1, // +1 for root
        separatorBuilder: (_, __) => const Icon(Icons.chevron_right, size: 16),
        itemBuilder: (ctx, index) {
          if (index == 0) {
            return TextButton(
              onPressed: () => ref
                  .read(directoryBrowserProvider.notifier)
                  .loadRoot(),
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                minimumSize: Size.zero,
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              child: const Text('Root'),
            );
          }
          final crumb = state.breadcrumbs[index - 1];
          final isLast = index == state.breadcrumbs.length;
          return TextButton(
            onPressed: isLast
                ? null
                : () => ref
                    .read(directoryBrowserProvider.notifier)
                    .navigateToBreadcrumb(index - 1),
            style: TextButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 4),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: Text(
              crumb.name,
              style: isLast
                  ? TextStyle(
                      color: theme.colorScheme.onSurface,
                      fontWeight: FontWeight.bold,
                    )
                  : null,
            ),
          );
        },
      ),
    );
  }

  Widget _buildErrorBanner(Object error, ThemeData theme) {
    // Not-configured is informational, not an error
    if (error is NotFoundException) {
      return Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.folder_off,
                size: 48,
                color: theme.colorScheme.onSurface.withOpacity(0.4)),
            const SizedBox(height: 12),
            Text(
              'Directory browsing is not available',
              textAlign: TextAlign.center,
              style: theme.textTheme.titleSmall,
            ),
            const SizedBox(height: 8),
            Text(
              'Set projects_root in the server config to enable directory browsing.',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurface.withOpacity(0.6)),
            ),
          ],
        ),
      );
    }

    final message = error is AuthException
        ? 'Authentication failed — please reconnect'
        : 'Failed to load directories: $error';

    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.error_outline,
              size: 48, color: theme.colorScheme.error),
          const SizedBox(height: 12),
          Text(
            message,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium
                ?.copyWith(color: theme.colorScheme.error),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: () =>
                ref.read(directoryBrowserProvider.notifier).refresh(),
            icon: const Icon(Icons.refresh),
            label: const Text('Retry'),
          ),
        ],
      ),
    );
  }

  Widget _buildDirectoryList(
    DirectoryBrowserState state,
    ScrollController scrollController,
    ThemeData theme,
  ) {
    if (state.atDepthLimit && state.children.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: Text(
            'Maximum directory depth reached',
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

    if (state.children.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.folder_open,
                  size: 48,
                  color: theme.colorScheme.onSurface.withOpacity(0.4)),
              const SizedBox(height: 12),
              const Text('No sub-directories here'),
            ],
          ),
        ),
      );
    }

    return Column(
      children: [
        if (state.atDepthLimit)
          Container(
            color: theme.colorScheme.tertiaryContainer,
            padding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Row(
              children: [
                Icon(Icons.info_outline,
                    size: 16,
                    color: theme.colorScheme.onTertiaryContainer),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Maximum depth reached — cannot navigate deeper',
                    style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onTertiaryContainer),
                  ),
                ),
              ],
            ),
          ),
        Expanded(
          child: ListView.builder(
            controller: scrollController,
            itemCount: state.children.length,
            itemBuilder: (ctx, i) {
              final entry = state.children[i];
              return _DirectoryListTile(
                entry: entry,
                atDepthLimit: state.atDepthLimit,
                onNavigate: state.atDepthLimit
                    ? null
                    : () => ref
                        .read(directoryBrowserProvider.notifier)
                        .navigateInto(entry),
                onSelect: () => Navigator.pop(context, entry),
              );
            },
          ),
        ),
      ],
    );
  }
}

/// A single row in the directory list.
class _DirectoryListTile extends StatelessWidget {
  final DirectoryEntry entry;
  final bool atDepthLimit;
  final VoidCallback? onNavigate;
  final VoidCallback onSelect;

  const _DirectoryListTile({
    required this.entry,
    required this.atDepthLimit,
    required this.onNavigate,
    required this.onSelect,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListTile(
      leading: const Icon(Icons.folder, semanticLabel: 'Folder'),
      title: Text(
        entry.name,
        // Display name only — never show raw path
        semanticsLabel: 'Directory: ${entry.name}',
      ),
      subtitle: entry.techStack != null
          ? Chip(
              label: Text(
                entry.techStack!,
                style: theme.textTheme.labelSmall,
              ),
              padding: EdgeInsets.zero,
              materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
              visualDensity: VisualDensity.compact,
            )
          : null,
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Navigate into chevron
          if (!atDepthLimit)
            IconButton(
              icon: const Icon(Icons.chevron_right),
              tooltip: 'Open ${entry.name}',
              onPressed: onNavigate,
            ),
          // Select this directory
          TextButton(
            onPressed: onSelect,
            child: const Text('Select'),
          ),
        ],
      ),
      onTap: atDepthLimit ? null : onNavigate,
    );
  }
}
