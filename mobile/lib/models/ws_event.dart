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
    return WsEvent(
      event: json['event'] as String? ?? '',
      ts: json['ts'] as String?,
      data: data,
    );
  }
}
