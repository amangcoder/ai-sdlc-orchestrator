/// Cost breakdown per agent role.
class AgentCost {
  final String agent;
  final double costUsd;

  const AgentCost({required this.agent, required this.costUsd});

  factory AgentCost.fromJson(Map<String, dynamic> json) {
    return AgentCost(
      agent: json['agent'] as String? ?? '',
      costUsd: (json['cost_usd'] as num?)?.toDouble() ?? 0.0,
    );
  }

  Map<String, dynamic> toJson() => {
        'agent': agent,
        'cost_usd': costUsd,
      };
}

/// Cost breakdown per model.
class ModelCost {
  final String model;
  final double costUsd;

  const ModelCost({required this.model, required this.costUsd});

  factory ModelCost.fromJson(Map<String, dynamic> json) {
    return ModelCost(
      model: json['model'] as String? ?? '',
      costUsd: (json['cost_usd'] as num?)?.toDouble() ?? 0.0,
    );
  }

  Map<String, dynamic> toJson() => {
        'model': model,
        'cost_usd': costUsd,
      };
}

/// Cost analytics summary returned by GET /api/v1/cost-analytics.
class CostAnalytics {
  final double totalSpendUsd;
  final double burnRateUsdPerHour;
  final double avgCostPerRun;
  final List<AgentCost> costByAgent;
  final List<ModelCost> costByModel;

  const CostAnalytics({
    required this.totalSpendUsd,
    required this.burnRateUsdPerHour,
    required this.avgCostPerRun,
    required this.costByAgent,
    required this.costByModel,
  });

  factory CostAnalytics.fromJson(Map<String, dynamic> json) {
    return CostAnalytics(
      totalSpendUsd: (json['total_spend_usd'] as num?)?.toDouble() ?? 0.0,
      burnRateUsdPerHour:
          (json['burn_rate_usd_per_hour'] as num?)?.toDouble() ?? 0.0,
      avgCostPerRun: (json['avg_cost_per_run'] as num?)?.toDouble() ?? 0.0,
      costByAgent: (json['cost_by_agent'] as List<dynamic>?)
              ?.map((dynamic e) =>
                  AgentCost.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
      costByModel: (json['cost_by_model'] as List<dynamic>?)
              ?.map((dynamic e) =>
                  ModelCost.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
    );
  }

  Map<String, dynamic> toJson() => {
        'total_spend_usd': totalSpendUsd,
        'burn_rate_usd_per_hour': burnRateUsdPerHour,
        'avg_cost_per_run': avgCostPerRun,
        'cost_by_agent': costByAgent.map((e) => e.toJson()).toList(),
        'cost_by_model': costByModel.map((e) => e.toJson()).toList(),
      };
}
