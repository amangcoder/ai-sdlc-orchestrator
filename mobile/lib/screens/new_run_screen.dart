import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/auth_provider.dart';
import '../providers/runs_provider.dart';
import '../models/run_summary.dart';
import '../models/directory_entry.dart';
import '../services/api_service.dart';
import '../services/flag_preferences_service.dart';

class NewRunScreen extends ConsumerStatefulWidget {
  const NewRunScreen({super.key});

  @override
  ConsumerState<NewRunScreen> createState() => _NewRunScreenState();
}

class _NewRunScreenState extends ConsumerState<NewRunScreen> {
  final _formKey = GlobalKey<FormState>();
  final _featureRequestController = TextEditingController();

  // Run config
  String _workflowType = 'feature_development';
  RunSummary? _activeRun;
  bool _isCheckingActiveRun = true;
  bool _isSubmitting = false;

  // Directory state
  List<DirectoryEntry> _directories = [];
  bool _isLoadingDirectories = true;
  String? _directoriesError;
  DirectoryEntry? _selectedDirectory;
  bool _staleDirBanner = false;

  // Existing flags
  bool _debate = false;
  bool _dryRun = false;
  bool _enhancedPerception = false;
  double _maxBudget = 50.0;

  // New flags — null means "inherit server config default"
  bool? _knowledge; // tristate: null=auto, true=on, false=off
  bool? _selfOrchestrate;
  bool? _confirm;
  bool? _techStackConfirmation;
  bool? _checklistVerify;
  int _maxConcurrentAgents = 0; // 0 = unlimited
  String? _mode;
  String? _phase;
  String? _fromPhase;
  String? _logFormat;
  int _researchers = 3;
  int _brainstormers = 3;
  int _debateRounds = 2;

  final _prefs = FlagPreferencesService();

  static const _workflowTypes = [
    ('feature_development', 'Feature Development'),
    ('bugfix', 'Bug Fix'),
    ('refactor', 'Refactor'),
    ('performance_optimization', 'Performance Optimization'),
    ('security_audit', 'Security Audit'),
  ];

  static const _modes = [
    (null, '(default)'),
    ('fast', 'Fast'),
    ('superhaiku', 'Super Haiku'),
    ('supersonnet', 'Super Sonnet'),
    ('balanced', 'Balanced'),
    ('overkill', 'Overkill'),
  ];

  static const _phases = [
    (null, '(all phases)'),
    ('pm', 'Product Manager'),
    ('architect', 'System Architect'),
    ('engineer', 'Engineer'),
    ('qa', 'QA'),
    ('reviewer', 'Reviewer'),
  ];

  @override
  void initState() {
    super.initState();
    _checkActiveRun();
    _loadDirectories();
  }

  @override
  void dispose() {
    _featureRequestController.dispose();
    super.dispose();
  }

  bool get _phaseConflict => _phase != null && _fromPhase != null;

  bool get _canSubmit =>
      _selectedDirectory != null &&
      !_isSubmitting &&
      !_isCheckingActiveRun &&
      _activeRun == null &&
      !_phaseConflict &&
      _directoriesError == null &&
      !_isLoadingDirectories;

  int get _customFlagCount {
    int count = 0;
    if (_debate) count++;
    if (_dryRun) count++;
    if (_enhancedPerception) count++;
    if (_maxBudget != 50.0) count++;
    if (_knowledge != null) count++;
    if (_selfOrchestrate != null) count++;
    if (_confirm != null) count++;
    if (_techStackConfirmation != null) count++;
    if (_checklistVerify != null) count++;
    if (_maxConcurrentAgents != 0) count++;
    if (_mode != null) count++;
    if (_phase != null) count++;
    if (_fromPhase != null) count++;
    if (_logFormat != null) count++;
    if (_debate && _researchers != 3) count++;
    if (_debate && _brainstormers != 3) count++;
    if (_debate && _debateRounds != 2) count++;
    return count;
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

  Future<void> _loadDirectories() async {
    setState(() {
      _isLoadingDirectories = true;
      _directoriesError = null;
    });
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) {
        if (mounted) setState(() => _isLoadingDirectories = false);
        return;
      }
      final api = ApiService(credentials: creds);
      final dirs = await api.getDirectories();
      if (!mounted) return;

      final lastDirId = await _prefs.loadLastDirectoryId();
      DirectoryEntry? preSelected;
      bool stale = false;

      if (lastDirId != null) {
        preSelected = dirs.where((d) => d.id == lastDirId).firstOrNull;
        if (preSelected == null && dirs.isNotEmpty) stale = true;
      }
      preSelected ??= dirs.isNotEmpty ? dirs.first : null;

      setState(() {
        _directories = dirs;
        _selectedDirectory = preSelected;
        _staleDirBanner = stale;
        _isLoadingDirectories = false;
      });

      if (preSelected != null) {
        await _loadFlagsForDirectory(preSelected.id);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _directoriesError =
              'Could not load directories — check server connection';
          _isLoadingDirectories = false;
        });
      }
    }
  }

  Future<void> _loadFlagsForDirectory(String dirId) async {
    final flags = await _prefs.loadFlags(dirId);
    if (!mounted || flags == null) return;
    setState(() {
      _debate = flags['debate'] as bool? ?? false;
      _dryRun = flags['dry_run'] as bool? ?? false;
      _enhancedPerception = flags['enhanced_perception'] as bool? ?? false;
      _maxBudget = (flags['max_budget_usd'] as num?)?.toDouble() ?? 50.0;
      _knowledge = flags['knowledge'] as bool?;
      _selfOrchestrate = flags['self_orchestrate'] as bool?;
      _confirm = flags['confirm'] as bool?;
      _techStackConfirmation = flags['tech_stack_confirmation'] as bool?;
      _checklistVerify = flags['checklist_verify'] as bool?;
      _maxConcurrentAgents = flags['max_concurrent_agents'] as int? ?? 0;
      _mode = flags['mode'] as String?;
      _phase = flags['phase'] as String?;
      _fromPhase = flags['from_phase'] as String?;
      _logFormat = flags['log_format'] as String?;
      _researchers = flags['researchers'] as int? ?? 3;
      _brainstormers = flags['brainstormers'] as int? ?? 3;
      _debateRounds = flags['debate_rounds'] as int? ?? 2;
    });
  }

  Future<void> _saveFlagsForCurrentDirectory() async {
    if (_selectedDirectory == null) return;
    final flags = <String, dynamic>{
      'debate': _debate,
      'dry_run': _dryRun,
      'enhanced_perception': _enhancedPerception,
      'max_budget_usd': _maxBudget,
      if (_knowledge != null) 'knowledge': _knowledge,
      if (_selfOrchestrate != null) 'self_orchestrate': _selfOrchestrate,
      if (_confirm != null) 'confirm': _confirm,
      if (_techStackConfirmation != null)
        'tech_stack_confirmation': _techStackConfirmation,
      if (_checklistVerify != null) 'checklist_verify': _checklistVerify,
      'max_concurrent_agents': _maxConcurrentAgents,
      if (_mode != null) 'mode': _mode,
      if (_phase != null) 'phase': _phase,
      if (_fromPhase != null) 'from_phase': _fromPhase,
      if (_logFormat != null) 'log_format': _logFormat,
      'researchers': _researchers,
      'brainstormers': _brainstormers,
      'debate_rounds': _debateRounds,
    };
    await _prefs.saveFlags(_selectedDirectory!.id, flags);
  }

  void _resetFlags() {
    setState(() {
      _debate = false;
      _dryRun = false;
      _enhancedPerception = false;
      _maxBudget = 50.0;
      _knowledge = null;
      _selfOrchestrate = null;
      _confirm = null;
      _techStackConfirmation = null;
      _checklistVerify = null;
      _maxConcurrentAgents = 0;
      _mode = null;
      _phase = null;
      _fromPhase = null;
      _logFormat = null;
      _researchers = 3;
      _brainstormers = 3;
      _debateRounds = 2;
    });
  }

  Future<void> _onDirectorySelected(DirectoryEntry dir) async {
    await _saveFlagsForCurrentDirectory();
    setState(() {
      _selectedDirectory = dir;
      _staleDirBanner = false;
    });
    await _prefs.saveLastDirectoryId(dir.id);
    await _loadFlagsForDirectory(dir.id);
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (!_canSubmit) return;

    setState(() => _isSubmitting = true);
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) {
        if (mounted) context.go('/settings');
        return;
      }

      await _saveFlagsForCurrentDirectory();

      final api = ApiService(credentials: creds);
      final payload = <String, dynamic>{
        'feature_request': _featureRequestController.text.trim(),
        'workflow_type': _workflowType,
        'workspace_id': _selectedDirectory!.id,
        'debate': _debate,
        'dry_run': _dryRun,
        'enhanced_perception': _enhancedPerception,
        'max_budget_usd': _maxBudget,
        if (_knowledge != null) 'knowledge': _knowledge,
        if (_selfOrchestrate != null) 'self_orchestrate': _selfOrchestrate,
        if (_confirm != null) 'confirm': _confirm,
        if (_techStackConfirmation != null)
          'tech_stack_confirmation': _techStackConfirmation,
        if (_checklistVerify != null) 'checklist_verify': _checklistVerify,
        if (_maxConcurrentAgents > 0)
          'max_concurrent_agents': _maxConcurrentAgents,
        if (_mode != null) 'mode': _mode,
        if (_phase != null) 'phase': _phase,
        if (_fromPhase != null) 'from_phase': _fromPhase,
        if (_logFormat != null) 'log_format': _logFormat,
        if (_debate && _researchers != 3) 'researchers': _researchers,
        if (_debate && _brainstormers != 3) 'brainstormers': _brainstormers,
        if (_debate && _debateRounds != 2) 'debate_rounds': _debateRounds,
      };

      final result = await api.startRun(payload);
      final newRunId = result['run_id'] as String?;
      if (newRunId != null && mounted) {
        ref.read(runsNotifierProvider.notifier).refresh();
        context.go('/runs/$newRunId');
      }
    } on ConflictException catch (e) {
      await _checkActiveRun();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(
              'Run ${e.activeRunId?.substring(0, 8) ?? "unknown"} is already active'),
        ));
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

  void _showDirectoryPicker() {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => _DirectoryPickerSheet(
        directories: _directories,
        selectedId: _selectedDirectory?.id,
        onSelected: (dir) {
          Navigator.pop(ctx);
          _onDirectorySelected(dir);
        },
      ),
    );
  }

  String get _runSummary {
    final dir = _selectedDirectory?.name ?? '…';
    final modeLabel = _mode ?? 'default model';
    final dryRunStr = _dryRun ? ' · dry-run' : '';
    final budget = '\$${_maxBudget.toStringAsFixed(0)} budget';
    final wf = _workflowTypes
        .firstWhere(
          (t) => t.$1 == _workflowType,
          orElse: () => (_workflowType, _workflowType),
        )
        .$2;
    return '$wf on $dir · $modeLabel$dryRunStr · $budget';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('New Run')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_activeRun != null) ...[
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
                    horizontal: 16, vertical: 8),
              ),
              const SizedBox(height: 16),
            ],
            if (_staleDirBanner) ...[
              MaterialBanner(
                content: const Text(
                    'Previously selected directory is no longer available'),
                actions: [
                  TextButton(
                    onPressed: () => setState(() => _staleDirBanner = false),
                    child: const Text('Dismiss'),
                  ),
                ],
                backgroundColor: Colors.amber.shade100,
                padding: const EdgeInsets.symmetric(
                    horizontal: 16, vertical: 8),
              ),
              const SizedBox(height: 16),
            ],
            Form(
              key: _formKey,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
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

                  // Directory selection card
                  _buildDirectoryCard(theme),
                  const SizedBox(height: 16),

                  // Workflow type
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

                  // Advanced options
                  _buildAdvancedOptions(theme),
                  const SizedBox(height: 8),

                  // Phase conflict error
                  if (_phaseConflict)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(
                        'Cannot use Phase and Start-from-Phase together',
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: theme.colorScheme.error),
                      ),
                    ),

                  // Run summary
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Text(
                      _runSummary,
                      style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant),
                    ),
                  ),

                  // Submit button
                  ElevatedButton(
                    onPressed: _canSubmit ? _submit : null,
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

  Widget _buildDirectoryCard(ThemeData theme) {
    if (_isLoadingDirectories) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(16),
          child: Row(children: [
            SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2)),
            SizedBox(width: 12),
            Text('Loading directories...'),
          ]),
        ),
      );
    }

    if (_directoriesError != null) {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                _directoriesError!,
                style: theme.textTheme.bodyMedium
                    ?.copyWith(color: theme.colorScheme.error),
              ),
              const SizedBox(height: 8),
              TextButton(
                  onPressed: _loadDirectories, child: const Text('Retry')),
            ],
          ),
        ),
      );
    }

    return Card(
      child: Padding(
        padding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Directory',
                      style: theme.textTheme.labelSmall),
                  const SizedBox(height: 2),
                  Text(
                    _selectedDirectory?.name ?? 'No directory selected',
                    style: theme.textTheme.bodyLarge,
                  ),
                  if (_selectedDirectory?.techStack != null)
                    Chip(
                      label: Text(
                        _selectedDirectory!.techStack!,
                        style: theme.textTheme.labelSmall,
                      ),
                      padding: EdgeInsets.zero,
                      materialTapTargetSize:
                          MaterialTapTargetSize.shrinkWrap,
                      visualDensity: VisualDensity.compact,
                    ),
                ],
              ),
            ),
            TextButton(
              onPressed:
                  _directories.isEmpty ? null : _showDirectoryPicker,
              child: const Text('Change'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAdvancedOptions(ThemeData theme) {
    final badgeCount = _customFlagCount;
    return Theme(
      data: theme.copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        title: Row(
          children: [
            const Text('Advanced Options'),
            if (badgeCount > 0) ...[
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: theme.colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  '$badgeCount custom',
                  style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.onPrimaryContainer),
                ),
              ),
            ],
          ],
        ),
        initiallyExpanded: false,
        children: [
          // ── Execution Controls ──────────────────────────────────
          _sectionLabel('Execution Controls', theme),
          SwitchListTile(
            title: const Text('Dry run'),
            subtitle: const Text(
                'Plan without executing — no API calls to agents'),
            value: _dryRun,
            onChanged: (v) => setState(() => _dryRun = v),
          ),
          _cyclingSwitchTile(
            'Self-orchestrate',
            'Let the AI design the optimal pipeline before executing',
            _selfOrchestrate,
            (v) => setState(() => _selfOrchestrate = v),
          ),
          _cyclingSwitchTile(
            'Confirm',
            'Review each agent prompt before execution',
            _confirm,
            (v) => setState(() => _confirm = v),
          ),
          _phaseDropdown('Phase', _phase,
              (v) => setState(() => _phase = v)),
          _phaseDropdown('Start from phase', _fromPhase,
              (v) => setState(() => _fromPhase = v)),
          const Divider(height: 1),

          // ── Budget & Resources ──────────────────────────────────
          _sectionLabel('Budget & Resources', theme),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Max budget: \$${_maxBudget.toStringAsFixed(0)}',
                  style: theme.textTheme.bodyMedium,
                ),
                Slider(
                  value: _maxBudget,
                  min: 1,
                  max: 500,
                  divisions: 50,
                  label: '\$${_maxBudget.toStringAsFixed(0)}',
                  onChanged: (v) => setState(() => _maxBudget = v),
                ),
                if ((_mode == 'overkill' || _mode == 'supersonnet') &&
                    _maxBudget < 50)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Chip(
                      label: const Text(
                          'High-powered models typically cost \$50–150 per run'),
                      backgroundColor: Colors.amber.shade100,
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                Row(
                  children: [
                    const Expanded(
                        child: Text('Max concurrent agents')),
                    _stepper(
                      _maxConcurrentAgents,
                      0,
                      100,
                      (v) => setState(() => _maxConcurrentAgents = v),
                    ),
                  ],
                ),
                Text(
                  '0 = unlimited',
                  style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant),
                ),
                const SizedBox(height: 8),
              ],
            ),
          ),
          const Divider(height: 1),

          // ── Debate ──────────────────────────────────────────────
          _sectionLabel('Debate', theme),
          SwitchListTile(
            title: const Text('Debate mode'),
            subtitle: const Text(
                'Enable multi-agent debate for complex decisions'),
            value: _debate,
            onChanged: (v) => setState(() => _debate = v),
          ),
          if (_debate)
            Padding(
              padding: const EdgeInsets.only(left: 32, right: 16),
              child: Column(
                children: [
                  _labeledStepper('Researchers', _researchers, 1, 10,
                      (v) => setState(() => _researchers = v)),
                  _labeledStepper('Brainstormers', _brainstormers, 1,
                      10, (v) => setState(() => _brainstormers = v)),
                  _labeledStepper('Debate rounds', _debateRounds, 1,
                      10, (v) => setState(() => _debateRounds = v)),
                ],
              ),
            ),
          const Divider(height: 1),

          // ── Knowledge & Quality ─────────────────────────────────
          _sectionLabel('Knowledge & Quality', theme),
          _knowledgeTile(theme),
          SwitchListTile(
            title: const Text('Enhanced perception'),
            value: _enhancedPerception,
            onChanged: (v) =>
                setState(() => _enhancedPerception = v),
          ),
          _cyclingSwitchTile(
            'Checklist verify',
            'Run post-write quality checklist in planning phases',
            _checklistVerify,
            (v) => setState(() => _checklistVerify = v),
          ),
          _cyclingSwitchTile(
            'Tech stack confirmation',
            'Pause after Architecture to review tech stack',
            _techStackConfirmation,
            (v) => setState(() => _techStackConfirmation = v),
          ),
          const Divider(height: 1),

          // ── Model & Logging ──────────────────────────────────────
          _sectionLabel('Model & Logging', theme),
          Padding(
            padding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: DropdownButtonFormField<String?>(
              value: _mode,
              decoration: const InputDecoration(
                labelText: 'Model routing mode',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              items: _modes
                  .map((m) => DropdownMenuItem<String?>(
                      value: m.$1, child: Text(m.$2)))
                  .toList(),
              onChanged: (v) => setState(() => _mode = v),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
            child: Row(
              children: [
                Text('Log format', style: theme.textTheme.bodyMedium),
                const SizedBox(width: 12),
                Wrap(
                  spacing: 4,
                  children: [
                    for (final (val, label) in [
                      (null, 'Default'),
                      ('console', 'Console'),
                      ('json', 'JSON'),
                    ])
                      ChoiceChip(
                        label: Text(label),
                        selected: _logFormat == val,
                        onSelected: (_) =>
                            setState(() => _logFormat = val),
                        visualDensity: VisualDensity.compact,
                      ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 4),

          // Reset
          Padding(
            padding: const EdgeInsets.symmetric(
                horizontal: 16, vertical: 8),
            child: TextButton(
              onPressed: _resetFlags,
              child: const Text('Reset to defaults'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _sectionLabel(String label, ThemeData theme) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 2),
        child: Text(
          label,
          style: theme.textTheme.labelSmall
              ?.copyWith(color: theme.colorScheme.primary),
        ),
      );

  Widget _cyclingSwitchTile(
    String title,
    String subtitle,
    bool? value,
    void Function(bool?) onChanged,
  ) {
    final label = value == null ? 'Auto' : (value ? 'On' : 'Off');
    return ListTile(
      title: Text(title),
      subtitle: Text(subtitle),
      trailing: TextButton(
        onPressed: () {
          if (value == null) {
            onChanged(true);
          } else if (value) {
            onChanged(false);
          } else {
            onChanged(null);
          }
        },
        child: Text(label),
      ),
    );
  }

  Widget _knowledgeTile(ThemeData theme) {
    return ListTile(
      title: const Text('Knowledge'),
      subtitle: const Text('Knowledge base: Off / Auto (inherit) / On'),
      trailing: Wrap(
        spacing: 4,
        children: [
          for (final (val, label) in [
            (false, 'Off'),
            (null, 'Auto'),
            (true, 'On'),
          ])
            ChoiceChip(
              label: Text(label),
              selected: _knowledge == val,
              onSelected: (_) => setState(() => _knowledge = val),
              visualDensity: VisualDensity.compact,
            ),
        ],
      ),
    );
  }

  Widget _phaseDropdown(
    String label,
    String? value,
    void Function(String?) onChanged,
  ) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: DropdownButtonFormField<String?>(
        value: value,
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
          isDense: true,
        ),
        items: _phases
            .map((p) =>
                DropdownMenuItem<String?>(value: p.$1, child: Text(p.$2)))
            .toList(),
        onChanged: onChanged,
      ),
    );
  }

  Widget _stepper(int value, int min, int max, void Function(int) onChanged) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconButton(
          icon: const Icon(Icons.remove),
          iconSize: 18,
          onPressed:
              value > min ? () => onChanged(value - 1) : null,
        ),
        Text('$value', style: const TextStyle(fontSize: 16)),
        IconButton(
          icon: const Icon(Icons.add),
          iconSize: 18,
          onPressed:
              value < max ? () => onChanged(value + 1) : null,
        ),
      ],
    );
  }

  Widget _labeledStepper(
    String label,
    int value,
    int min,
    int max,
    void Function(int) onChanged,
  ) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(child: Text(label)),
          _stepper(value, min, max, onChanged),
        ],
      ),
    );
  }
}

// ── Directory Picker Bottom Sheet ───────────────────────────────────────────

class _DirectoryPickerSheet extends StatefulWidget {
  final List<DirectoryEntry> directories;
  final String? selectedId;
  final void Function(DirectoryEntry) onSelected;

  const _DirectoryPickerSheet({
    required this.directories,
    this.selectedId,
    required this.onSelected,
  });

  @override
  State<_DirectoryPickerSheet> createState() =>
      _DirectoryPickerSheetState();
}

class _DirectoryPickerSheetState extends State<_DirectoryPickerSheet> {
  final _searchController = TextEditingController();
  String _query = '';

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final filtered = _query.isEmpty
        ? widget.directories
        : widget.directories
            .where((d) =>
                d.name.toLowerCase().contains(_query.toLowerCase()))
            .toList();

    return DraggableScrollableSheet(
      initialChildSize: 0.6,
      minChildSize: 0.4,
      maxChildSize: 0.9,
      expand: false,
      builder: (ctx, scrollController) => Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 8, 8),
            child: Row(
              children: [
                Text(
                  'Select Directory',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const Spacer(),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.pop(ctx),
                ),
              ],
            ),
          ),
          if (widget.directories.length > 5)
            Padding(
              padding: const EdgeInsets.symmetric(
                  horizontal: 16, vertical: 4),
              child: TextField(
                controller: _searchController,
                decoration: const InputDecoration(
                  hintText: 'Search directories...',
                  prefixIcon: Icon(Icons.search),
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
                onChanged: (v) => setState(() => _query = v),
              ),
            ),
          Expanded(
            child: filtered.isEmpty
                ? const Center(child: Text('No directories found'))
                : ListView.builder(
                    controller: scrollController,
                    itemCount: filtered.length,
                    itemBuilder: (ctx, i) {
                      final dir = filtered[i];
                      return ListTile(
                        title: Text(dir.name),
                        subtitle: dir.techStack != null
                            ? Text(dir.techStack!)
                            : null,
                        trailing: dir.id == widget.selectedId
                            ? const Icon(Icons.check)
                            : null,
                        onTap: () => widget.onSelected(dir),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
