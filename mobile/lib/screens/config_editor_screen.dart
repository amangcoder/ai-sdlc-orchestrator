import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../models/config_model.dart';
import '../providers/auth_provider.dart';
import '../services/api_service.dart';
import '../widgets/json_tree_node.dart';
import '../widgets/error_state_widget.dart';

/// Config editor screen - loads the orchestrator config and allows editing
/// non-sensitive fields. Redacted fields are shown with a lock icon.
class ConfigEditorScreen extends ConsumerStatefulWidget {
  const ConfigEditorScreen({super.key});

  @override
  ConsumerState<ConfigEditorScreen> createState() => _ConfigEditorScreenState();
}

class _ConfigEditorScreenState extends ConsumerState<ConfigEditorScreen> {
  ConfigModel? _config;
  bool _isLoading = true;
  bool _isSaving = false;
  Object? _error;
  String? _saveError;
  final Map<String, dynamic> _changes = {};

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  Future<void> _loadConfig() async {
    setState(() { _isLoading = true; _error = null; _saveError = null; });
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) {
        if (mounted) context.go('/settings');
        return;
      }
      final api = ApiService(credentials: creds);
      final config = await api.getConfig();
      if (mounted) {
        setState(() {
          _config = config;
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

  Future<bool> _confirmDiscardChanges() async {
    if (_changes.isEmpty) return true;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Discard Unsaved Changes?'),
        content: const Text(
          'You have unsaved changes. Refreshing will discard them.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Keep Editing'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Discard'),
          ),
        ],
      ),
    );
    return confirmed ?? false;
  }

  Future<void> _onRefresh() async {
    final discard = await _confirmDiscardChanges();
    if (!discard) return;
    _changes.clear();
    await _loadConfig();
  }

  Future<void> _saveChanges() async {
    if (_changes.isEmpty) return;
    setState(() { _isSaving = true; _saveError = null; });
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) return;
      final api = ApiService(credentials: creds);
      await api.updateConfig(_changes);
      if (mounted) {
        _changes.clear();
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Config saved — takes effect on next run'),
            backgroundColor: Colors.green,
          ),
        );
        setState(() {});
      }
    } on ServerException catch (e) {
      if (mounted) {
        setState(() => _saveError = e.message);
      }
    } on AuthException {
      await ref.read(authNotifierProvider.notifier).clear();
      if (mounted) context.go('/settings');
    } catch (e) {
      if (mounted) setState(() => _saveError = e.toString());
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  void _onValueChanged(String topLevelKey, dynamic newValue) {
    setState(() {
      _changes[topLevelKey] = newValue;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Config Editor')),
      body: _buildBody(context),
      floatingActionButton: !_isLoading &&
              _error == null &&
              _changes.isNotEmpty
          ? FloatingActionButton.extended(
              onPressed: _isSaving ? null : _saveChanges,
              icon: _isSaving
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: Colors.white,
                      ),
                    )
                  : const Icon(Icons.save),
              label: Text(_isSaving ? 'Saving...' : 'Save Changes'),
            )
          : null,
    );
  }

  Widget _buildBody(BuildContext context) {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_error != null) {
      return ErrorStateWidget.fromException(
        _error!,
        onRetry: _loadConfig,
        onNavigate: context.go,
      );
    }

    final config = _config!;

    return RefreshIndicator(
      onRefresh: _onRefresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(16),
        children: [
          // Save error banner
          if (_saveError != null)
            Card(
              color: Colors.red.shade50,
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Icon(Icons.error, color: Colors.red, size: 16),
                        const SizedBox(width: 8),
                        Text(
                          'Save failed',
                          style: TextStyle(
                            color: Colors.red.shade700,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      _saveError!,
                      style: TextStyle(color: Colors.red.shade700),
                    ),
                  ],
                ),
              ),
            ),
          if (_saveError != null) const SizedBox(height: 12),

          // Unsaved changes indicator
          if (_changes.isNotEmpty)
            Chip(
              label: Text(
                '${_changes.length} unsaved change${_changes.length > 1 ? 's' : ''}',
              ),
              avatar: const Icon(Icons.edit, size: 14),
              backgroundColor: Colors.orange.shade100,
            ),
          if (_changes.isNotEmpty) const SizedBox(height: 12),

          // Redacted keys info
          if (config.redactedKeys.isNotEmpty)
            Text(
              'Sensitive fields (${config.redactedKeys.length} redacted): '
              '${config.redactedKeys.join(', ')}',
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    color: Colors.grey,
                    fontStyle: FontStyle.italic,
                  ),
            ),
          const SizedBox(height: 8),

          // Editable config tree
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: EditableJsonTreeNode(
                value: config.config,
                depth: 0,
                topLevelKey: 'root',
                onChanged: _onValueChanged,
              ),
            ),
          ),
          // Space for FAB
          const SizedBox(height: 80),
        ],
      ),
    );
  }
}
