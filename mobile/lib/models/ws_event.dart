import 'pending_prompt.dart';

class WsEvent {
  final String event;
  final String? ts;
  final Map<String, dynamic> data;

  const WsEvent({
    required this.event,
    this.ts,
    required this.data,
  });

  factory WsEvent.fromJson(Map<String, dynamic> json) {
    final data = Map<String, dynamic>.from(json);
    // Defensive fallback: check 'type' if 'event' is absent, for forward
    // compatibility with potential backend key changes (architecture note).
    final eventType =
        json['event'] as String? ?? json['type'] as String? ?? '';
    return WsEvent(
      event: eventType,
      ts: json['ts'] as String? ?? json['timestamp'] as String?,
      data: data,
    );
  }

  /// Returns a [PendingPrompt] when this event is a 'prompt_pending' event.
  ///
  /// Returns null for all other event types.  This ensures no regression on
  /// existing event types (run_status_changed, log_line, task_result, etc.).
  PendingPrompt? get promptPending {
    if (event != 'prompt_pending') return null;
    final prompt = data['prompt'];
    if (prompt == null || prompt is! Map<String, dynamic>) return null;
    try {
      return PendingPrompt.fromJson(prompt);
    } catch (_) {
      return null;
    }
  }

  /// Convenience getter — true when [event] is 'prompt_pending' and the
  /// prompt payload is parseable.
  bool get isPromptPending => promptPending != null;
}
