import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/slo_report_model.dart';
import '../providers/slo_provider.dart';
import '../widgets/error_state_widget.dart';

/// Displays SLO compliance — 6 SLI rows with color-coded status indicators.
///
/// REQ-008, AC-005: Fetches GET /api/v1/slo and renders:
///   - Each SLI as a ListTile with name, current value vs target,
///     color-coded leading icon (green/amber/red), and error budget percentage
///   - Pull-to-refresh support
class SLOComplianceScreen extends ConsumerStatefulWidget {
  const SLOComplianceScreen({super.key});

  @override
  ConsumerState<SLOComplianceScreen> createState() =>
      _SLOComplianceScreenState();
}

class _SLOComplianceScreenState extends ConsumerState<SLOComplianceScreen> {
  Future<void> _onRefresh() async {
    await ref.read(sloNotifierProvider.notifier).refresh();
  }

  /// Returns the icon color for a given SLI status.
  Color _statusColor(BuildContext context, String status) {
    switch (status) {
      case 'breached':
        return Theme.of(context).colorScheme.error;
      case 'at_risk':
        return Colors.amber;
      case 'passing':
      default:
        return Colors.green;
    }
  }

  /// Returns the icon for a given SLI status.
  IconData _statusIcon(String status) {
    switch (status) {
      case 'breached':
        return Icons.cancel;
      case 'at_risk':
        return Icons.warning_amber_rounded;
      case 'passing':
      default:
        return Icons.check_circle;
    }
  }

  /// Returns a human-readable label for SLI status.
  String _statusLabel(String status) {
    switch (status) {
      case 'breached':
        return 'Breached';
      case 'at_risk':
        return 'At Risk';
      case 'passing':
      default:
        return 'Passing';
    }
  }

  @override
  Widget build(BuildContext context) {
    final sloAsync = ref.watch(sloNotifierProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('SLO Compliance'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _onRefresh,
          ),
        ],
      ),
      body: sloAsync.when(
        loading: () =>
            const Center(child: CircularProgressIndicator()),
        error: (error, _) => ErrorStateWidget.fromException(
          error,
          onRetry: _onRefresh,
        ),
        data: (report) {
          if (report == null || report.slis.isEmpty) {
            return RefreshIndicator(
              onRefresh: _onRefresh,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: const [
                  Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: Text('No SLO data available.')),
                  ),
                ],
              ),
            );
          }

          return RefreshIndicator(
            onRefresh: _onRefresh,
            child: ListView.separated(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: report.slis.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final sli = report.slis[index];
                final color = _statusColor(context, sli.status);
                final icon = _statusIcon(sli.status);
                final statusLabel = _statusLabel(sli.status);

                return Semantics(
                  label:
                      '${sli.name}: ${statusLabel}. Current: ${sli.currentValue.toStringAsFixed(1)}%, Target: ${sli.target.toStringAsFixed(1)}%',
                  child: ListTile(
                    leading: Tooltip(
                      message: statusLabel,
                      child: Icon(icon, color: color, semanticLabel: statusLabel),
                    ),
                    title: Text(sli.name),
                    subtitle: Text(
                      'Current: ${sli.currentValue.toStringAsFixed(1)}% '
                      '(Target ≥ ${sli.target.toStringAsFixed(1)}%)',
                    ),
                    trailing: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        Text(
                          statusLabel,
                          style: Theme.of(context)
                              .textTheme
                              .labelSmall
                              ?.copyWith(
                                color: color,
                                fontWeight: FontWeight.bold,
                              ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          'Budget: ${sli.errorBudgetRemainingPct.toStringAsFixed(1)}%',
                          style: Theme.of(context)
                              .textTheme
                              .labelSmall
                              ?.copyWith(
                                color: Theme.of(context)
                                    .colorScheme
                                    .onSurface
                                    .withOpacity(0.6),
                              ),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}
