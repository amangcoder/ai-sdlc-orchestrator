import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../models/run_detail.dart';
import '../models/phase_state.dart';
import '../models/ws_event.dart';
import '../providers/auth_provider.dart';
import '../services/api_service.dart';
import '../services/secure_storage_service.dart';
import '../services/websocket_service.dart';
import '../widgets/phase_list_item.dart';
import '../widgets/connection_status_chip.dart';
import '../widgets/error_state_widget.dart';

/// Canonical phase ordering for display.
const _kPhaseOrder = [
  'competitor_research',
  'user_psychology_research',
  'ux_specification',
  'prd',
  'architecture',
  'engineering_plan',
  'task_breakdown',
  'implementation',
  'qa',
  'code_review',
];

/// Detailed view of a single orchestration run with live phase updates.
class RunDetailScreen extends ConsumerStatefulWidget {
  final String runId;

  const RunDetailScreen({super.key, required this.runId});

  @override
  ConsumerState<RunDetailScreen> createState() => _RunDetailScreenState();
}

class _RunDetailScreenState extends ConsumerState<RunDetailScreen> {
  RunDetail? _runDetail;
  Map<String, PhaseState> _phaseMap = {};
  bool _isLoading = true;
  Object? _error;
  WebSocketService? _wsService;
  StreamSubscription<WsEvent>? _wsSub;
  StreamSubscription<ConnectionStatus>? _statusSub;
  ConnectionStatus _connectionStatus = ConnectionStatus.disconnected;
  Timer? _cancelPollTimer;
  ScaffoldFeatureController<SnackBar, SnackBarClosedReason>? _cancelSnackbar;
  bool _isCancelling = false;

  @override
  void initState() {
    super.initState();
    _loadRun();
  }

  @override
  void dispose() {
    _wsSub?.cancel();
    _statusSub?.cancel();
    _wsService?.disconnect();
    _cancelPollTimer?.cancel();
    super.dispose();
  }

  Future<void> _loadRun() async {
    if (mounted) setState(() { _isLoading = true; _error = null; });
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) {
        if (mounted) context.go('/settings');
        return;
      }
      final api = ApiService(credentials: creds);
      final run = await api.getRun(widget.runId);
      if (mounted) {
        setState(() {
          _runDetail = run;
          _phaseMap = Map.from(run.phases);
          _isLoading = false;
        });
        if (run.status == 'running') {
          _connectWebSocket(creds);
        }
      }
    } on AuthException {
      await ref.read(authNotifierProvider.notifier).clear();
      if (mounted) context.go('/settings');
    } catch (e) {
      if (mounted) setState(() { _error = e; _isLoading = false; });
    }
  }

  void _connectWebSocket(Credentials credentials) {
    _wsService = WebSocketService(
      baseUrl: credentials.url,
      apiKey: credentials.apiKey,
    );

    // IMPORTANT: call connect() first — it initialises the internal stream
    // controllers (statusStream, eventStream) synchronously before returning.
    // Subscribing to statusStream BEFORE connect() would throw a null-check
    // error because _statusStream is null until connect() runs.
    _wsSub = _wsService!.connect(widget.runId).listen(
      (event) => _handleWsEvent(event),
      onError: (_) {},
    );

    _statusSub = _wsService!.statusStream.listen((status) {
      if (mounted) setState(() => _connectionStatus = status);
    });

    setState(() => _connectionStatus = ConnectionStatus.connected);
  }

  void _handleWsEvent(WsEvent event) {
    if (!mounted) return;
    setState(() {
      // Update phase states from events
      if (event.event == 'phase_start' || event.event == 'task_invoke') {
        final phase = event.data['phase'] as String? ??
            event.data['step'] as String?;
        if (phase != null) {
          _phaseMap[phase] = PhaseState(
            status: 'running',
            costUsd: _phaseMap[phase]?.costUsd,
            modelTier: event.data['model_tier'] as String?,
          );
        }
      } else if (event.event == 'task_result' ||
          event.event == 'phase_complete') {
        final phase = event.data['phase'] as String? ??
            event.data['step'] as String?;
        if (phase != null) {
          final cost = (event.data['cost_usd'] as num?)?.toDouble() ??
              _phaseMap[phase]?.costUsd;
          _phaseMap[phase] = PhaseState(
            status: 'completed',
            costUsd: cost,
            modelTier: event.data['model_tier'] as String?,
          );
        }
      } else if (event.event == 'run_complete' ||
          event.event == 'stream_end') {
        _wsService?.disconnect();
        _loadRun();
      }
    });
  }

  Future<void> _confirmCancel() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Cancel Run?'),
        content: const Text(
          'This will request cancellation. The run may take up to 2 minutes to stop.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Keep Running'),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.red,
              foregroundColor: Colors.white,
            ),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Cancel Run'),
          ),
        ],
      ),
    );

    if (confirmed != true) return;
    await _cancelRun();
  }

  Future<void> _cancelRun() async {
    if (_isCancelling) return;
    setState(() => _isCancelling = true);

    // Show persistent snackbar immediately
    _cancelSnackbar = ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text(
          'Cancellation in progress — may take up to 2 minutes',
        ),
        duration: Duration(minutes: 5),
      ),
    );

    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) return;
      final api = ApiService(credentials: creds);
      await api.cancelRun(widget.runId);
    } catch (_) {
      // Ignore cancel errors — still poll for status
    }

    // Poll until run is no longer active
    _cancelPollTimer = Timer.periodic(const Duration(seconds: 5), (_) async {
      try {
        final creds = await ref.read(authNotifierProvider.future);
        if (creds == null) return;
        final api = ApiService(credentials: creds);
        final run = await api.getRun(widget.runId);
        if (run.status == 'failed' || run.status == 'cancelled') {
          _cancelPollTimer?.cancel();
          _cancelSnackbar?.close();
          if (mounted) {
            setState(() {
              _runDetail = run;
              _phaseMap = Map.from(run.phases);
              _isCancelling = false;
            });
          }
        }
      } catch (_) {}
    });
  }

  Future<void> _resumeRun() async {
    try {
      final creds = await ref.read(authNotifierProvider.future);
      if (creds == null) return;
      final api = ApiService(credentials: creds);
      final result = await api.resumeRun(widget.runId);
      final newRunId = result['run_id'] as String?;
      if (newRunId != null && mounted) {
        context.pushReplacement('/runs/$newRunId');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Resume failed: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return Scaffold(
        appBar: AppBar(title: Text(widget.runId.substring(0, 8))),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    if (_error != null) {
      return Scaffold(
        appBar: AppBar(title: Text(widget.runId.substring(0, 8))),
        body: ErrorStateWidget.fromException(
          _error!,
          onRetry: _loadRun,
          onNavigate: context.go,
        ),
      );
    }

    final run = _runDetail!;
    final isActive = run.status == 'running';

    // Sort phases by canonical order, then remaining alphabetically
    final sortedPhases = run.phases.keys.toList()
      ..sort((a, b) {
        final ai = _kPhaseOrder.indexOf(a);
        final bi = _kPhaseOrder.indexOf(b);
        if (ai == -1 && bi == -1) return a.compareTo(b);
        if (ai == -1) return 1;
        if (bi == -1) return -1;
        return ai.compareTo(bi);
      });

    return Scaffold(
      appBar: AppBar(
        title: Text(run.runId.length >= 8
            ? run.runId.substring(0, 8)
            : run.runId),
        actions: [
          if (isActive)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
              child: ConnectionStatusChip(status: _connectionStatus),
            ),
          IconButton(
            icon: const Icon(Icons.live_tv),
            tooltip: 'Live events',
            onPressed: () => context.push('/runs/${run.runId}/events'),
          ),
          IconButton(
            icon: const Icon(Icons.folder_open),
            tooltip: 'Artifacts',
            onPressed: () => context.push('/runs/${run.runId}/artifacts'),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Header card
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          run.workflowType.replaceAll('_', ' '),
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ),
                      _StatusChip(status: run.status),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    run.featureRequest,
                    style: Theme.of(context).textTheme.bodySmall,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 12),
                  _InfoRow(
                    icon: Icons.schedule,
                    label: 'Started',
                    value: run.startTime != null
                        ? _formatDateTime(run.startTime!)
                        : '—',
                  ),
                  if (run.endTime != null)
                    _InfoRow(
                      icon: Icons.check_circle_outline,
                      label: 'Ended',
                      value: _formatDateTime(run.endTime!),
                    ),
                  if (run.totalCostUsd != null)
                    _InfoRow(
                      icon: Icons.attach_money,
                      label: 'Total cost',
                      value: '\$${run.totalCostUsd!.toStringAsFixed(4)}',
                    ),
                  if (run.stepsTotal > 0)
                    _InfoRow(
                      icon: Icons.checklist,
                      label: 'Progress',
                      value:
                          '${run.stepsCompleted} / ${run.stepsTotal} steps',
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          // Phase list
          Text(
            'Phases',
            style: Theme.of(context).textTheme.titleSmall,
          ),
          const SizedBox(height: 8),
          Card(
            child: Column(
              children: [
                if (sortedPhases.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(16),
                    child: Text('No phase data available'),
                  ),
                for (final key in sortedPhases) ...[
                  PhaseListItem(
                    phaseName: key,
                    phase: _phaseMap[key] ?? run.phases[key]!,
                  ),
                  if (key != sortedPhases.last) const Divider(height: 1),
                ],
              ],
            ),
          ),
          const SizedBox(height: 16),

          // Actions
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              if (run.status == 'failed')
                ElevatedButton.icon(
                  onPressed: _resumeRun,
                  icon: const Icon(Icons.replay),
                  label: const Text('Resume'),
                ),
              if (isActive) ...[
                const SizedBox(width: 8),
                ElevatedButton.icon(
                  onPressed: _isCancelling ? null : _confirmCancel,
                  icon: const Icon(Icons.stop),
                  label: const Text('Cancel'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.red,
                    foregroundColor: Colors.white,
                  ),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  String _formatDateTime(DateTime dt) {
    return '${dt.year}-${dt.month.toString().padLeft(2,'0')}-'
        '${dt.day.toString().padLeft(2,'0')} '
        '${dt.hour.toString().padLeft(2,'0')}:'
        '${dt.minute.toString().padLeft(2,'0')}';
  }
}

class _StatusChip extends StatelessWidget {
  final String status;
  // ignore: use_key_in_widget_constructors
  const _StatusChip({required this.status});

  @override
  Widget build(BuildContext context) {
    Color color;
    switch (status) {
      case 'running':
        color = Colors.blue;
      case 'completed':
        color = Colors.green;
      case 'failed':
        color = Colors.red;
      case 'cancelled':
        color = Colors.grey;
      default:
        color = Colors.grey;
    }
    return Chip(
      label: Text(
        status,
        style: const TextStyle(fontSize: 12, color: Colors.white),
      ),
      backgroundColor: color,
      padding: EdgeInsets.zero,
      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
    );
  }
}

class _InfoRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  // ignore: use_key_in_widget_constructors
  const _InfoRow({required this.icon, required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Icon(icon, size: 16, color: Colors.grey),
          const SizedBox(width: 8),
          Text(
            '$label: ',
            style: Theme.of(context).textTheme.labelSmall,
          ),
          Expanded(
            child: Text(
              value,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}
