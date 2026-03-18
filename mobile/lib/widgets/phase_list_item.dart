import 'package:flutter/material.dart';
import '../models/phase_state.dart';

/// Displays a single phase's name, status, cost, and error summary.
class PhaseListItem extends StatelessWidget {
  final String phaseName;
  final PhaseState phase;

  const PhaseListItem({
    super.key,
    required this.phaseName,
    required this.phase,
  });

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: _StatusIcon(status: phase.status),
      title: Text(
        _formatPhaseName(phaseName),
        style: Theme.of(context).textTheme.bodyMedium,
      ),
      subtitle: phase.status == 'failed' && phase.error != null
          ? Text(
              phase.error!.length > 80
                  ? '${phase.error!.substring(0, 80)}…'
                  : phase.error!,
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(color: Colors.red),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            )
          : null,
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        mainAxisSize: MainAxisSize.min,
        children: [
          if (phase.costUsd != null && phase.costUsd! > 0.0)
            Text(
              '\$${phase.costUsd!.toStringAsFixed(4)}',
              style: Theme.of(context).textTheme.labelSmall,
            ),
          if (phase.modelTier != null)
            Text(
              phase.modelTier!,
              style: Theme.of(context)
                  .textTheme
                  .labelSmall
                  ?.copyWith(color: Colors.grey),
            ),
        ],
      ),
    );
  }

  String _formatPhaseName(String name) {
    return name
        .replaceAll('_', ' ')
        .split(' ')
        .map((w) => w.isEmpty ? w : w[0].toUpperCase() + w.substring(1))
        .join(' ');
  }
}

class _StatusIcon extends StatelessWidget {
  final String status;

  // ignore: use_key_in_widget_constructors
  const _StatusIcon({required this.status});

  @override
  Widget build(BuildContext context) {
    switch (status) {
      case 'running':
        return const SizedBox(
          width: 20,
          height: 20,
          child: CircularProgressIndicator(
            strokeWidth: 2,
            color: Colors.blue,
          ),
        );
      case 'completed':
        return const Icon(Icons.check_circle, color: Colors.green, size: 20);
      case 'failed':
        return const Icon(Icons.cancel, color: Colors.red, size: 20);
      case 'skipped':
        return const Icon(Icons.remove, color: Colors.grey, size: 20);
      case 'pending':
      default:
        return const Icon(Icons.schedule, color: Colors.grey, size: 20);
    }
  }
}
