/// An alert returned by GET /api/v1/alerts.
class Alert {
  final String id;

  /// Severity is one of: 'critical', 'warning', 'info'.
  final String severity;
  final String message;
  final DateTime triggeredAt;

  /// Status is one of: 'active', 'resolved', 'acknowledged'.
  final String status;

  const Alert({
    required this.id,
    required this.severity,
    required this.message,
    required this.triggeredAt,
    required this.status,
  });

  factory Alert.fromJson(Map<String, dynamic> json) {
    return Alert(
      id: json['id'] as String? ?? '',
      severity: json['severity'] as String? ?? 'info',
      message: json['message'] as String? ?? '',
      triggeredAt: json['triggered_at'] != null
          ? DateTime.tryParse(json['triggered_at'] as String) ?? DateTime.now()
          : DateTime.now(),
      status: json['status'] as String? ?? 'active',
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'severity': severity,
        'message': message,
        'triggered_at': triggeredAt.toIso8601String(),
        'status': status,
      };
}
