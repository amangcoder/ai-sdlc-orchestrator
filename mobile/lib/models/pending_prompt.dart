/// Represents a pending prompt that the orchestrator is waiting for the user
/// to answer. Returned by GET /api/v1/runs/{run_id}/pending-prompt and
/// delivered via the 'prompt_pending' WebSocket event.
class PendingPrompt {
  final String promptId;
  final String question;

  /// Either 'free_text' or 'single_choice'.
  final String type;

  /// Non-null when [type] is 'single_choice'; null for 'free_text'.
  final List<String>? options;
  final DateTime createdAt;

  const PendingPrompt({
    required this.promptId,
    required this.question,
    required this.type,
    this.options,
    required this.createdAt,
  });

  factory PendingPrompt.fromJson(Map<String, dynamic> json) {
    final optionsRaw = json['options'];
    final List<String>? parsedOptions;
    if (optionsRaw is List) {
      parsedOptions = optionsRaw.map((e) => e.toString()).toList();
    } else {
      parsedOptions = null;
    }

    return PendingPrompt(
      promptId: json['prompt_id'] as String? ?? '',
      question: json['question'] as String? ?? '',
      type: json['type'] as String? ?? 'free_text',
      options: parsedOptions,
      createdAt: json['created_at'] != null
          ? DateTime.parse(json['created_at'] as String)
          : DateTime.now(),
    );
  }

  Map<String, dynamic> toJson() => {
        'prompt_id': promptId,
        'question': question,
        'type': type,
        'options': options,
        'created_at': createdAt.toIso8601String(),
      };
}
