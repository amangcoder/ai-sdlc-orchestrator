import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../models/ws_event.dart';
import '../providers/auth_provider.dart';
import '../services/api_service.dart';
import '../services/secure_storage_service.dart';
import '../services/websocket_service.dart';

/// Full event stream view with WebSocket streaming and polling fallback.
class LiveEventsScreen extends ConsumerStatefulWidget {
  final String runId;

  const LiveEventsScreen({super.key, required this.runId});

  @override
  ConsumerState<LiveEventsScreen> createState() => _LiveEventsScreenState();
}

class _LiveEventsScreenState extends ConsumerState<LiveEventsScreen> {
  final List<WsEvent> _events = [];
  final ScrollController _scrollController = ScrollController();
  WebSocketService? _wsService;
  StreamSubscription<WsEvent>? _wsSub;
  StreamSubscription<ConnectionStatus>? _statusSub;
  ConnectionStatus _connectionStatus = ConnectionStatus.disconnected;
  int _lastIndex = 0;
  Timer? _pollTimer;
  bool _showJumpFab = false;
  DateTime? _runStartTime;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _connect();
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    _wsSub?.cancel();
    _statusSub?.cancel();
    _wsService?.disconnect();
    _pollTimer?.cancel();
    super.dispose();
  }

  void _onScroll() {
    final isAtBottom = _isAtBottom();
    if (_showJumpFab == isAtBottom) {
      setState(() => _showJumpFab = !isAtBottom);
    }
  }

  bool _isAtBottom() {
    if (!_scrollController.hasClients) return true;
    return _scrollController.position.pixels >=
        _scrollController.position.maxScrollExtent - 80;
  }

  void _scrollToBottom() {
    if (!_scrollController.hasClients) return;
    _scrollController.animateTo(
      _scrollController.position.maxScrollExtent,
      duration: const Duration(milliseconds: 500),
      curve: Curves.easeOut,
    );
    setState(() => _showJumpFab = false);
  }

  Future<void> _connect() async {
    final creds = await ref.read(authNotifierProvider.future);
    if (creds == null) {
      if (mounted) context.go('/settings');
      return;
    }

    _wsService = WebSocketService(
      baseUrl: creds.url,
      apiKey: creds.apiKey,
    );

    // IMPORTANT: call connect() first — it initialises the internal stream
    // controllers (statusStream, eventStream) synchronously before returning.
    // Subscribing to statusStream BEFORE connect() would throw a null-check
    // error because _statusStream is null until connect() runs.
    _wsSub = _wsService!
        .connect(widget.runId, afterLine: _lastIndex)
        .listen(_onEvent);

    _statusSub = _wsService!.statusStream.listen((status) {
      if (!mounted) return;
      setState(() => _connectionStatus = status);

      if (status == ConnectionStatus.polling) {
        _startPolling(creds);
      } else if (status == ConnectionStatus.connected) {
        _stopPolling();
      }
    });

    setState(() => _connectionStatus = ConnectionStatus.connected);
  }

  void _onEvent(WsEvent event) {
    if (!mounted) return;
    _lastIndex++;

    // Capture run start time from the first run_start event for relative
    // timestamp display (e.g., "+2m 30s").
    if (event.event == 'run_start' && event.ts != null && _runStartTime == null) {
      _runStartTime = DateTime.tryParse(event.ts!);
    }
    // If we still don't have a start time, use the first event's timestamp.
    _runStartTime ??= event.ts != null ? DateTime.tryParse(event.ts!) : null;

    setState(() => _events.add(event));

    if (_isAtBottom()) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _scrollController.hasClients) {
          _scrollController.animateTo(
            _scrollController.position.maxScrollExtent,
            duration: const Duration(milliseconds: 500),
            curve: Curves.easeOut,
          );
        }
      });
    }
  }

  void _startPolling(Credentials credentials) {
    _pollTimer?.cancel();
    final api = ApiService(credentials: credentials);
    _pollTimer = Timer.periodic(const Duration(seconds: 10), (_) async {
      try {
        final result = await api.getEvents(widget.runId, offset: _lastIndex);
        final events = result['events'] as List<dynamic>? ?? [];
        for (final e in events) {
          // _onEvent increments _lastIndex internally — no double-count here
          _onEvent(WsEvent.fromJson(e as Map<String, dynamic>));
        }
      } catch (_) {}
    });
  }

  void _stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              widget.runId.length >= 8
                  ? widget.runId.substring(0, 8)
                  : widget.runId,
            ),
            Text(
              _connectionStatusLabel,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    color: _connectionStatusColor,
                  ),
            ),
          ],
        ),
      ),
      body: _events.isEmpty
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const CircularProgressIndicator(),
                  const SizedBox(height: 16),
                  Text(
                    'Waiting for events...',
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                ],
              ),
            )
          : ListView.builder(
              controller: _scrollController,
              padding: const EdgeInsets.symmetric(vertical: 4),
              itemCount: _events.length,
              itemBuilder: (context, index) {
                return _EventRow(
                  event: _events[index],
                  runStartTime: _runStartTime,
                );
              },
            ),
      floatingActionButton: _showJumpFab
          ? FloatingActionButton.small(
              onPressed: _scrollToBottom,
              tooltip: 'Jump to bottom',
              child: const Icon(Icons.keyboard_double_arrow_down),
            )
          : null,
    );
  }

  String get _connectionStatusLabel {
    switch (_connectionStatus) {
      case ConnectionStatus.connected:
        return 'Live';
      case ConnectionStatus.reconnecting:
        return 'Reconnecting...';
      case ConnectionStatus.polling:
        return 'Polling';
      case ConnectionStatus.disconnected:
        return 'Disconnected';
    }
  }

  Color get _connectionStatusColor {
    switch (_connectionStatus) {
      case ConnectionStatus.connected:
        return Colors.green;
      case ConnectionStatus.reconnecting:
        return Colors.orange;
      case ConnectionStatus.polling:
        return Colors.grey;
      case ConnectionStatus.disconnected:
        return Colors.red;
    }
  }
}

class _EventRow extends StatelessWidget {
  final WsEvent event;
  final DateTime? runStartTime;

  // ignore: use_key_in_widget_constructors
  const _EventRow({required this.event, required this.runStartTime});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Event type label
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(
              color: _eventColor.withOpacity(0.15),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(
              event.event,
              style: TextStyle(
                fontSize: 10,
                fontFamily: 'monospace',
                color: _eventColor,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Log message (for log/log_line events)
                if (event.event == 'log' || event.event == 'log_line')
                  Text(
                    event.data['message'] as String? ?? '',
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          fontFamily: 'monospace',
                          fontSize: 11,
                        ),
                    maxLines: 5,
                    overflow: TextOverflow.ellipsis,
                  ),
                // Step name and agent role
                if (event.data['step'] != null ||
                    event.data['agent_role'] != null)
                  Text(
                    [
                      event.data['step'],
                      event.data['agent_role'],
                    ]
                        .whereType<String>()
                        .join(' · '),
                    style: Theme.of(context)
                        .textTheme
                        .bodySmall
                        ?.copyWith(fontWeight: FontWeight.w500),
                  ),
                // Cost (task_result only)
                if (event.event == 'task_result' &&
                    event.data['cost_usd'] != null)
                  Text(
                    '\$${(event.data['cost_usd'] as num).toStringAsFixed(4)}',
                    style: Theme.of(context)
                        .textTheme
                        .labelSmall
                        ?.copyWith(color: Colors.grey),
                  ),
              ],
            ),
          ),
          // Relative timestamp (from run start) or absolute if start unknown
          if (event.ts != null)
            Text(
              runStartTime != null
                  ? _relativeTime(event.ts!)
                  : _absoluteTime(event.ts!),
              style: Theme.of(context)
                  .textTheme
                  .labelSmall
                  ?.copyWith(color: Colors.grey),
            ),
        ],
      ),
    );
  }

  String _relativeTime(String isoTs) {
    if (runStartTime == null) return '';
    try {
      final ts = DateTime.parse(isoTs);
      final diff = ts.difference(runStartTime!);
      final m = diff.inMinutes;
      final s = diff.inSeconds.remainder(60);
      return '+${m}m ${s}s';
    } catch (_) {
      return '';
    }
  }

  String _absoluteTime(String isoTs) {
    try {
      final ts = DateTime.parse(isoTs);
      return '${ts.hour.toString().padLeft(2, '0')}:'
          '${ts.minute.toString().padLeft(2, '0')}:'
          '${ts.second.toString().padLeft(2, '0')}';
    } catch (_) {
      return '';
    }
  }

  Color get _eventColor {
    switch (event.event) {
      case 'run_start':
        return Colors.blue;
      case 'task_invoke':
        return Colors.orange;
      case 'task_result':
        return Colors.green;
      case 'run_complete':
        return Colors.purple;
      case 'stream_end':
        return Colors.purple;
      default:
        return Colors.grey;
    }
  }
}
