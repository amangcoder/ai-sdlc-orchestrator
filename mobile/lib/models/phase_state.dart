class PhaseState {
  final String status;
  final double? costUsd;
  final String? error;
  final String? modelTier;

  const PhaseState({
    required this.status,
    this.costUsd,
    this.error,
    this.modelTier,
  });

  factory PhaseState.fromJson(Map<String, dynamic> json) {
    return PhaseState(
      status: json['status'] as String? ?? 'pending',
      costUsd: (json['cost_usd'] as num?)?.toDouble(),
      error: json['error'] as String?,
      modelTier: json['model_tier'] as String?,
    );
  }
}
