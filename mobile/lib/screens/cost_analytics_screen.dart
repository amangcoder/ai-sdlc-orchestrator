import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/cost_analytics_model.dart';
import '../providers/cost_analytics_provider.dart';
import '../widgets/error_state_widget.dart';

/// Displays cost analytics KPI cards and a per-agent cost breakdown list.
///
/// REQ-007, AC-004: Fetches GET /api/v1/cost-analytics and displays:
///   - Total spend, burn rate, and average cost per run as KPI cards
///   - Cost-by-agent list with USD amounts
///   - Pull-to-refresh support
class CostAnalyticsScreen extends ConsumerStatefulWidget {
  const CostAnalyticsScreen({super.key});

  @override
  ConsumerState<CostAnalyticsScreen> createState() =>
      _CostAnalyticsScreenState();
}

class _CostAnalyticsScreenState extends ConsumerState<CostAnalyticsScreen> {
  Future<void> _onRefresh() async {
    await ref.read(costAnalyticsNotifierProvider.notifier).refresh();
  }

  @override
  Widget build(BuildContext context) {
    final analyticsAsync = ref.watch(costAnalyticsNotifierProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Cost Analytics'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _onRefresh,
          ),
        ],
      ),
      body: analyticsAsync.when(
        loading: () =>
            const Center(child: CircularProgressIndicator()),
        error: (error, _) => ErrorStateWidget.fromException(
          error,
          onRetry: _onRefresh,
        ),
        data: (analytics) {
          if (analytics == null) {
            return const Center(
              child: Text('No cost analytics data available.'),
            );
          }
          return RefreshIndicator(
            onRefresh: _onRefresh,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(16),
              children: [
                // ── KPI Cards ────────────────────────────────────────────
                Semantics(
                  label: 'Cost analytics KPI cards',
                  child: Row(
                    children: [
                      Expanded(
                        child: _KpiCard(
                          label: 'Total Spend',
                          value: '\$${analytics.totalSpendUsd.toStringAsFixed(2)}',
                          icon: Icons.attach_money,
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: _KpiCard(
                          label: 'Burn Rate',
                          value:
                              '\$${analytics.burnRateUsdPerHour.toStringAsFixed(3)}/hr',
                          icon: Icons.local_fire_department,
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: _KpiCard(
                          label: 'Avg / Run',
                          value:
                              '\$${analytics.avgCostPerRun.toStringAsFixed(3)}',
                          icon: Icons.bar_chart,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 24),

                // ── Cost by Agent ─────────────────────────────────────────
                Text(
                  'Cost by Agent',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                      ),
                ),
                const SizedBox(height: 8),
                if (analytics.costByAgent.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 16),
                    child: Center(child: Text('No agent cost data available.')),
                  )
                else
                  ...analytics.costByAgent.map(
                    (agentCost) => _AgentCostTile(agentCost: agentCost),
                  ),

                const SizedBox(height: 24),

                // ── Cost by Model ─────────────────────────────────────────
                Text(
                  'Cost by Model',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                      ),
                ),
                const SizedBox(height: 8),
                if (analytics.costByModel.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 16),
                    child: Center(child: Text('No model cost data available.')),
                  )
                else
                  ...analytics.costByModel.map(
                    (modelCost) => _ModelCostTile(modelCost: modelCost),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

// ── KPI Card ──────────────────────────────────────────────────────────────────

class _KpiCard extends StatelessWidget {
  final String label;
  final String value;
  final IconData icon;

  const _KpiCard({
    required this.label,
    required this.value,
    required this.icon,
  });

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '$label: $value',
      child: Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                icon,
                size: 28,
                color: Theme.of(context).colorScheme.primary,
              ),
              const SizedBox(height: 8),
              Text(
                value,
                style: Theme.of(context).textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.bold,
                    ),
                textAlign: TextAlign.center,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 4),
              Text(
                label,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Theme.of(context)
                          .colorScheme
                          .onSurface
                          .withOpacity(0.6),
                    ),
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Agent Cost Tile ───────────────────────────────────────────────────────────

class _AgentCostTile extends StatelessWidget {
  final AgentCost agentCost;

  const _AgentCostTile({required this.agentCost});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Agent ${agentCost.agent} cost: \$${agentCost.costUsd.toStringAsFixed(4)}',
      child: ListTile(
        leading: const Icon(Icons.smart_toy_outlined),
        title: Text(agentCost.agent),
        trailing: Text(
          '\$${agentCost.costUsd.toStringAsFixed(4)}',
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                fontWeight: FontWeight.w600,
                color: Theme.of(context).colorScheme.primary,
              ),
        ),
        dense: true,
      ),
    );
  }
}

// ── Model Cost Tile ───────────────────────────────────────────────────────────

class _ModelCostTile extends StatelessWidget {
  final ModelCost modelCost;

  const _ModelCostTile({required this.modelCost});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Model ${modelCost.model} cost: \$${modelCost.costUsd.toStringAsFixed(4)}',
      child: ListTile(
        leading: const Icon(Icons.memory_outlined),
        title: Text(modelCost.model),
        trailing: Text(
          '\$${modelCost.costUsd.toStringAsFixed(4)}',
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                fontWeight: FontWeight.w600,
                color: Theme.of(context).colorScheme.primary,
              ),
        ),
        dense: true,
      ),
    );
  }
}
