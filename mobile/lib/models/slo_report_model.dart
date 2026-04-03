/// A single Service Level Indicator result.
class SliResult {
  final String name;
  final double target;
  final double currentValue;

  /// Status is one of: 'passing', 'at_risk', 'breached'.
  final String status;
  final double errorBudgetRemainingPct;

  const SliResult({
    required this.name,
    required this.target,
    required this.currentValue,
    required this.status,
    required this.errorBudgetRemainingPct,
  });

  factory SliResult.fromJson(Map<String, dynamic> json) {
    return SliResult(
      name: json['name'] as String? ?? '',
      target: (json['target'] as num?)?.toDouble() ?? 0.0,
      currentValue: (json['current_value'] as num?)?.toDouble() ?? 0.0,
      status: json['status'] as String? ?? 'passing',
      errorBudgetRemainingPct:
          (json['error_budget_remaining_pct'] as num?)?.toDouble() ?? 100.0,
    );
  }

  Map<String, dynamic> toJson() => {
        'name': name,
        'target': target,
        'current_value': currentValue,
        'status': status,
        'error_budget_remaining_pct': errorBudgetRemainingPct,
      };
}

/// SLO compliance report returned by GET /api/v1/slo.
class SloReport {
  final List<SliResult> slis;

  const SloReport({required this.slis});

  factory SloReport.fromJson(Map<String, dynamic> json) {
    return SloReport(
      slis: (json['slis'] as List<dynamic>?)
              ?.map((dynamic e) =>
                  SliResult.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
    );
  }

  Map<String, dynamic> toJson() => {
        'slis': slis.map((e) => e.toJson()).toList(),
      };
}
