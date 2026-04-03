import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/alert_model.dart';
import '../providers/alerts_provider.dart';
import '../widgets/error_state_widget.dart';

/// Displays a filterable list of alerts with severity icons.
///
/// REQ-009: Fetches GET /api/v1/alerts and displays:
///   - Severity filter chip row (All, Critical, Warning, Info)
///   - Alert list with severity icon, message, and formatted timestamp
///   - Empty state when no alerts match the current filter
///   - Pull-to-refresh support
class AlertsScreen extends ConsumerStatefulWidget {
  const AlertsScreen({super.key});

  @override
  ConsumerState<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends ConsumerState<AlertsScreen> {
  String _selectedSeverity = 'all';

  Future<void> _onRefresh() async {
    await ref.read(alertsNotifierProvider.notifier).refresh();
  }

  List<Alert> _filteredAlerts(List<Alert> alerts) {
    if (_selectedSeverity == 'all') return alerts;
    return alerts
        .where((a) => a.severity == _selectedSeverity)
        .toList();
  }

  IconData _severityIcon(String severity) {
    switch (severity) {
      case 'critical':
        return Icons.error;
      case 'warning':
        return Icons.warning_amber_rounded;
      case 'info':
      default:
        return Icons.info_outline;
    }
  }

  Color _severityColor(BuildContext context, String severity) {
    switch (severity) {
      case 'critical':
        return Theme.of(context).colorScheme.error;
      case 'warning':
        return Colors.amber;
      case 'info':
      default:
        return Theme.of(context).colorScheme.primary;
    }
  }

  String _formatTimestamp(DateTime dt) {
    final diff = DateTime.now().difference(dt);
    if (diff.inMinutes < 1) return 'just now';
    if (diff.inHours < 1) return '${diff.inMinutes}m ago';
    if (diff.inDays < 1) return '${diff.inHours}h ago';
    if (diff.inDays < 7) return '${diff.inDays}d ago';
    return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-'
        '${dt.day.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final alertsAsync = ref.watch(alertsNotifierProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Alerts'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _onRefresh,
          ),
        ],
      ),
      body: Column(
        children: [
          // ── Severity filter chips ─────────────────────────────────────
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Semantics(
              label: 'Filter alerts by severity',
              child: Row(
                children: [
                  for (final severity in [
                    'all',
                    'critical',
                    'warning',
                    'info',
                  ])
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: FilterChip(
                        label: Text(
                          severity == 'all'
                              ? 'All'
                              : severity[0].toUpperCase() +
                                  severity.substring(1),
                        ),
                        selected: _selectedSeverity == severity,
                        onSelected: (_) {
                          setState(() => _selectedSeverity = severity);
                        },
                      ),
                    ),
                ],
              ),
            ),
          ),

          // ── Alerts list ───────────────────────────────────────────────
          Expanded(
            child: alertsAsync.when(
              loading: () =>
                  const Center(child: CircularProgressIndicator()),
              error: (error, _) => ErrorStateWidget.fromException(
                error,
                onRetry: _onRefresh,
              ),
              data: (alerts) {
                final filtered = _filteredAlerts(alerts);

                if (filtered.isEmpty) {
                  return RefreshIndicator(
                    onRefresh: _onRefresh,
                    child: ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      children: [
                        Padding(
                          padding: const EdgeInsets.all(32),
                          child: Center(
                            child: Text(
                              _selectedSeverity == 'all'
                                  ? 'No alerts.'
                                  : 'No $_selectedSeverity alerts.',
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                          ),
                        ),
                      ],
                    ),
                  );
                }

                return RefreshIndicator(
                  onRefresh: _onRefresh,
                  child: ListView.separated(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    itemCount: filtered.length,
                    separatorBuilder: (_, __) =>
                        const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final alert = filtered[index];
                      final color =
                          _severityColor(context, alert.severity);
                      final icon = _severityIcon(alert.severity);
                      final severityLabel = alert.severity[0].toUpperCase() +
                          alert.severity.substring(1);

                      return Semantics(
                        label:
                            '$severityLabel alert: ${alert.message}, ${_formatTimestamp(alert.triggeredAt)}',
                        child: ListTile(
                          leading: Tooltip(
                            message: severityLabel,
                            child: Icon(
                              icon,
                              color: color,
                              semanticLabel: severityLabel,
                            ),
                          ),
                          title: Text(alert.message),
                          subtitle: Row(
                            children: [
                              Text(_formatTimestamp(alert.triggeredAt)),
                              const SizedBox(width: 8),
                              _StatusBadge(status: alert.status),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

// ── Status Badge ──────────────────────────────────────────────────────────────

class _StatusBadge extends StatelessWidget {
  final String status;

  const _StatusBadge({required this.status});

  @override
  Widget build(BuildContext context) {
    Color bg;
    switch (status) {
      case 'resolved':
        bg = Colors.green.withOpacity(0.15);
        break;
      case 'acknowledged':
        bg = Colors.blue.withOpacity(0.15);
        break;
      case 'active':
      default:
        bg = Theme.of(context).colorScheme.errorContainer.withOpacity(0.5);
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        status[0].toUpperCase() + status.substring(1),
        style: Theme.of(context).textTheme.labelSmall,
      ),
    );
  }
}
