import 'run_summary.dart';
import 'phase_state.dart';

class RunDetail extends RunSummary {
  final Map<String, PhaseState> phases;
  final List<dynamic> workflowTasks;

  const RunDetail({
    required super.runId,
    required super.featureRequest,
    required super.workflowType,
    required super.status,
    super.currentStep,
    super.totalCostUsd,
    super.startTime,
    super.endTime,
    super.stepsCompleted,
    super.stepsTotal,
    required this.phases,
    required this.workflowTasks,
  });

  factory RunDetail.fromJson(Map<String, dynamic> json) {
    final phasesJson = json['phases'] as Map<String, dynamic>? ?? {};
    final phases = phasesJson.map(
      (key, value) => MapEntry(key, PhaseState.fromJson(value as Map<String, dynamic>)),
    );
    return RunDetail(
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
      phases: phases,
      workflowTasks: json['workflow_tasks'] as List<dynamic>? ?? [],
    );
  }
}
