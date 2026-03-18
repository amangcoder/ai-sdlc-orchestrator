class RunSummary {
  final String runId;
  final String featureRequest;
  final String workflowType;
  final String status;
  final String? currentStep;
  final double? totalCostUsd;
  final DateTime? startTime;
  final DateTime? endTime;
  final int stepsCompleted;
  final int stepsTotal;

  const RunSummary({
    required this.runId,
    required this.featureRequest,
    required this.workflowType,
    required this.status,
    this.currentStep,
    this.totalCostUsd,
    this.startTime,
    this.endTime,
    this.stepsCompleted = 0,
    this.stepsTotal = 0,
  });

  factory RunSummary.fromJson(Map<String, dynamic> json) {
    return RunSummary(
      runId: json['run_id'] as String,
      featureRequest: json['feature_request'] as String? ?? '',
      workflowType: json['workflow_type'] as String? ?? '',
      status: json['status'] as String? ?? 'unknown',
      currentStep: json['current_step'] as String?,
      totalCostUsd: (json['total_cost_usd'] as num?)?.toDouble(),
      startTime: json['start_time'] != null
          ? DateTime.tryParse(json['start_time'] as String)
          : null,
      endTime: json['end_time'] != null
          ? DateTime.tryParse(json['end_time'] as String)
          : null,
      stepsCompleted: json['steps_completed'] as int? ?? 0,
      stepsTotal: json['steps_total'] as int? ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
    'run_id': runId,
    'feature_request': featureRequest,
    'workflow_type': workflowType,
    'status': status,
    'current_step': currentStep,
    'total_cost_usd': totalCostUsd,
    'start_time': startTime?.toIso8601String(),
    'end_time': endTime?.toIso8601String(),
    'steps_completed': stepsCompleted,
    'steps_total': stepsTotal,
  };
}
