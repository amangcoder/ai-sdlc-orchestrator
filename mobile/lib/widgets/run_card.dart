import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../models/run_summary.dart';

/// Card widget displaying a summary of an orchestration run.
class RunCard extends StatefulWidget {
  final RunSummary run;

  const RunCard({super.key, required this.run});

  @override
  State<RunCard> createState() => _RunCardState();
}

class _RunCardState extends State<RunCard>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulseController;
  late final Animation<double> _pulseAnimation;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1),
    );
    _pulseAnimation = Tween<double>(begin: 0.4, end: 1.0).animate(
      CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut),
    );
    if (widget.run.status == 'running') {
      _pulseController.repeat(reverse: true);
    }
  }

  @override
  void didUpdateWidget(RunCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.run.status == 'running' && !_pulseController.isAnimating) {
      _pulseController.repeat(reverse: true);
    } else if (widget.run.status != 'running' &&
        _pulseController.isAnimating) {
      _pulseController.stop();
    }
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final run = widget.run;
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => context.push('/runs/${run.runId}'),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  // Run ID prefix
                  Text(
                    run.runId.length >= 8
                        ? run.runId.substring(0, 8)
                        : run.runId,
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                          fontFamily: 'monospace',
                          fontWeight: FontWeight.bold,
                        ),
                  ),
                  const SizedBox(width: 8),
                  // Workflow type chip
                  Flexible(
                    child: Chip(
                      label: Text(
                        _formatWorkflowType(run.workflowType),
                        style: const TextStyle(fontSize: 11),
                        overflow: TextOverflow.ellipsis,
                      ),
                      backgroundColor:
                          _workflowColor(context, run.workflowType),
                      padding: EdgeInsets.zero,
                      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                  ),
                  const SizedBox(width: 8),
                  // Status indicator
                  _StatusIndicator(
                    status: run.status,
                    pulseAnimation: _pulseAnimation,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              // Feature request preview
              Text(
                run.featureRequest,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  // Current step (if running)
                  if (run.status == 'running' &&
                      run.currentStep != null) ...[
                    const Icon(Icons.play_circle_outline,
                        size: 14, color: Colors.blue),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        'Running: ${run.currentStep}',
                        style: Theme.of(context)
                            .textTheme
                            .labelSmall
                            ?.copyWith(color: Colors.blue),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ] else if (run.currentStep != null) ...[
                    const Icon(Icons.info_outline, size: 14),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        run.currentStep!,
                        style: Theme.of(context).textTheme.labelSmall,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ] else
                    const Spacer(),
                  // Elapsed time
                  _ElapsedTime(
                    startTime: run.startTime,
                    endTime: run.endTime,
                    isRunning: run.status == 'running',
                  ),
                  const SizedBox(width: 12),
                  // Cost
                  if (run.totalCostUsd != null)
                    Text(
                      '\$${run.totalCostUsd!.toStringAsFixed(2)}',
                      style: Theme.of(context).textTheme.labelSmall,
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _formatWorkflowType(String type) {
    return type.replaceAll('_', ' ').split(' ').map((w) {
      if (w.isEmpty) return w;
      return w[0].toUpperCase() + w.substring(1);
    }).join(' ');
  }

  Color _workflowColor(BuildContext context, String type) {
    switch (type) {
      case 'feature_development':
        return Colors.blue.shade100;
      case 'bugfix':
        return Colors.red.shade100;
      case 'refactor':
        return Colors.purple.shade100;
      case 'performance_optimization':
        return Colors.orange.shade100;
      case 'security_audit':
        return Colors.red.shade200;
      default:
        return Colors.grey.shade200;
    }
  }
}

class _StatusIndicator extends StatelessWidget {
  final String status;
  final Animation<double> pulseAnimation;

  const _StatusIndicator({
    super.key,
    required this.status,
    required this.pulseAnimation,
  });

  @override
  Widget build(BuildContext context) {
    final color = _statusColor;
    final label = _statusLabel;

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (status == 'running')
          AnimatedBuilder(
            animation: pulseAnimation,
            builder: (context, _) => Opacity(
              opacity: pulseAnimation.value,
              child: Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: color,
                ),
              ),
            ),
          )
        else
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: color,
            ),
          ),
        const SizedBox(width: 4),
        Text(
          label,
          style: Theme.of(context)
              .textTheme
              .labelSmall
              ?.copyWith(color: color),
        ),
      ],
    );
  }

  Color get _statusColor {
    switch (status) {
      case 'running':
        return Colors.blue;
      case 'completed':
        return Colors.green;
      case 'failed':
        return Colors.red;
      case 'cancelled':
        return Colors.grey;
      default:
        return Colors.grey;
    }
  }

  String get _statusLabel {
    switch (status) {
      case 'running':
        return 'Running';
      case 'completed':
        return 'Completed';
      case 'failed':
        return 'Failed';
      case 'cancelled':
        return 'Cancelled';
      default:
        return status;
    }
  }
}

class _ElapsedTime extends StatefulWidget {
  final DateTime? startTime;
  final DateTime? endTime;
  final bool isRunning;

  // ignore: use_key_in_widget_constructors
  const _ElapsedTime({
    required this.startTime,
    required this.endTime,
    required this.isRunning,
  });

  @override
  State<_ElapsedTime> createState() => _ElapsedTimeState();
}

class _ElapsedTimeState extends State<_ElapsedTime> {
  late final Stream<int> _ticker;

  @override
  void initState() {
    super.initState();
    _ticker = Stream.periodic(const Duration(seconds: 1), (i) => i);
  }

  @override
  Widget build(BuildContext context) {
    if (widget.startTime == null) return const SizedBox.shrink();

    if (!widget.isRunning) {
      final duration = (widget.endTime ?? DateTime.now())
          .difference(widget.startTime!);
      return Text(
        _formatDuration(duration),
        style: Theme.of(context).textTheme.labelSmall,
      );
    }

    return StreamBuilder<int>(
      stream: _ticker,
      builder: (context, _) {
        final duration = DateTime.now().difference(widget.startTime!);
        return Text(
          _formatDuration(duration),
          style: Theme.of(context).textTheme.labelSmall,
        );
      },
    );
  }

  String _formatDuration(Duration d) {
    final h = d.inHours;
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$h:$m:$s';
  }
}
