import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/auth_provider.dart';
import '../providers/runs_provider.dart';
import '../models/run_summary.dart';
import '../services/api_service.dart';

/// Screen to start a new orchestration run with workflow selection.
class NewRunScreen extends ConsumerStatefulWidget {
  const NewRunScreen({super.key});

  @override
  ConsumerState<NewRunScreen> createState() => _NewRunScreenState();
}

class _NewRunScreenState extends ConsumerState<NewRunScreen> {
  final _formKey = GlobalKey<FormState>();
  final _featureRequestController = TextEditingController();
  String _workflowType = 'feature_development';
  bool _debate = false;
  bool _dryRun = false;
  bool _enhancedPerception = false;
  double _maxBudget = 50.0;
  bool _isSubmitting = false;
  RunSummary? _activeRun;
  bool _isCheckingActiveRun = true;

  static const _workflowTypes = [
    ('feature_development', 'Feature Development'),
    ('bugfix', 'Bug Fix'),
    ('refactor', 'Refactor'),
    ('performance_optimization', 'Performance Optimization'),
    ('security_audit', 'Security Audit'),
  ];

  @override
  void initState() {
    super.initState();
    _checkActiveRun();
  }

  @override
  void dispose() {
    _featureRequestController.dispose();
    super.dispose();
  }

  Future<void> _checkActiveRun() async {
    setState(() => _isCheckingActiveRun = true);
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) return;
      final api = ApiService(credentials: creds);
      final runs = await api.listRuns();
      final active = runs.where((r) => r.status == 'running').firstOrNull;
      if (mounted) {
        setState(() {
          _activeRun = active;
          _isCheckingActiveRun = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _isCheckingActiveRun = false);
    }
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (_activeRun != null) return;

    setState(() => _isSubmitting = true);
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) {
        context.go('/settings');
        return;
      }
      final api = ApiService(credentials: creds);
      final result = await api.startRun({
        'feature_request': _featureRequestController.text.trim(),
        'workflow_type': _workflowType,
        'debate': _debate,
        'dry_run': _dryRun,
        'enhanced_perception': _enhancedPerception,
        'max_budget_usd': _maxBudget,
      });
      final newRunId = result['run_id'] as String?;
      if (newRunId != null && mounted) {
        // Refresh runs list
        ref.read(runsNotifierProvider.notifier).refresh();
        context.go('/runs/$newRunId');
      }
    } on ConflictException catch (e) {
      // Refresh active run banner
      await _checkActiveRun();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              'Run ${e.activeRunId?.substring(0, 8) ?? "unknown"} is already active',
            ),
          ),
        );
      }
    } on AuthException {
      await ref.read(authNotifierProvider.notifier).clear();
      if (mounted) context.go('/settings');
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to start run: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isSubmitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('New Run')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Active run blocking banner
            if (_activeRun != null)
              MaterialBanner(
                content: Text(
                  'Run ${_activeRun!.runId.substring(0, 8)} is active — '
                  'cancel or wait before starting another',
                ),
                actions: [
                  TextButton(
                    onPressed: () =>
                        context.push('/runs/${_activeRun!.runId}'),
                    child: const Text('View Run'),
                  ),
                ],
                backgroundColor: Colors.orange.shade100,
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
              ),
            if (_activeRun != null) const SizedBox(height: 16),

            Form(
              key: _formKey,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Feature request field
                  TextFormField(
                    controller: _featureRequestController,
                    maxLines: 6,
                    maxLength: 10000,
                    decoration: InputDecoration(
                      labelText: 'Feature Request',
                      hintText: 'Describe what you want to build...',
                      alignLabelWithHint: true,
                      border: const OutlineInputBorder(),
                      counterText:
                          '${_featureRequestController.text.length}/10000',
                    ),
                    onChanged: (_) => setState(() {}),
                    validator: (value) {
                      if (value == null || value.trim().length < 10) {
                        return 'Feature request must be at least 10 characters';
                      }
                      if (value.trim().length > 10000) {
                        return 'Feature request must be at most 10000 characters';
                      }
                      return null;
                    },
                  ),
                  const SizedBox(height: 16),

                  // Workflow type dropdown
                  DropdownButtonFormField<String>(
                    value: _workflowType,
                    decoration: const InputDecoration(
                      labelText: 'Workflow Type',
                      border: OutlineInputBorder(),
                    ),
                    items: _workflowTypes
                        .map((t) => DropdownMenuItem(
                              value: t.$1,
                              child: Text(t.$2),
                            ))
                        .toList(),
                    onChanged: (v) =>
                        setState(() => _workflowType = v ?? _workflowType),
                  ),
                  const SizedBox(height: 16),

                  // Advanced options (collapsed by default)
                  Theme(
                    data: Theme.of(context)
                        .copyWith(dividerColor: Colors.transparent),
                    child: ExpansionTile(
                      title: const Text('Advanced Options'),
                      initiallyExpanded: false,
                      children: [
                        SwitchListTile(
                          title: const Text('Debate mode'),
                          subtitle: const Text(
                            'Enable multi-agent debate for complex decisions',
                          ),
                          value: _debate,
                          onChanged: (v) => setState(() => _debate = v),
                        ),
                        SwitchListTile(
                          title: const Text('Enhanced perception'),
                          value: _enhancedPerception,
                          onChanged: (v) =>
                              setState(() => _enhancedPerception = v),
                        ),
                        SwitchListTile(
                          title: const Text('Dry run'),
                          subtitle: const Text(
                            'Plan without executing — no API calls to agents',
                          ),
                          value: _dryRun,
                          onChanged: (v) => setState(() => _dryRun = v),
                        ),
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Max budget: \$${_maxBudget.toStringAsFixed(0)}',
                                style:
                                    Theme.of(context).textTheme.bodyMedium,
                              ),
                              Slider(
                                value: _maxBudget,
                                min: 1.0,
                                max: 500.0,
                                divisions: 50,
                                label:
                                    '\$${_maxBudget.toStringAsFixed(0)}',
                                onChanged: (v) =>
                                    setState(() => _maxBudget = v),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 8),
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),

                  // Submit button
                  ElevatedButton(
                    onPressed: (_activeRun != null ||
                            _isSubmitting ||
                            _isCheckingActiveRun)
                        ? null
                        : _submit,
                    style: ElevatedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                    ),
                    child: _isSubmitting
                        ? const Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: Colors.white,
                                ),
                              ),
                              SizedBox(width: 8),
                              Text('Starting...'),
                            ],
                          )
                        : const Text('Start Run'),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
