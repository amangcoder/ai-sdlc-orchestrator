import 'dart:async';
import 'dart:convert';
import 'dart:io';
import '../models/ws_event.dart';

enum ConnectionStatus {
  connected,
  reconnecting,
  polling,
  disconnected,
}

class WebSocketService {
  final String baseUrl;
  final String apiKey;

  WebSocketService({required this.baseUrl, required this.apiKey});

  WebSocket? _socket;
  StreamController<WsEvent>? _eventController;
  StreamController<ConnectionStatus>? _statusController;
  int _lastReceivedLineIndex = 0;
  bool _disposed = false;
  Timer? _reconnectTimer;

  /// Tracks the time the connection first dropped; cleared on successful reconnect.
  /// Using this (rather than per-attempt timestamps) ensures the 30s polling
  /// threshold counts from the ORIGINAL disconnect, not the latest failed attempt.
  DateTime? _initialDisconnectTime;

  Stream<WsEvent>? _eventStream;
  Stream<ConnectionStatus>? _statusStream;

  Stream<WsEvent> get eventStream => _eventStream!;
  Stream<ConnectionStatus> get statusStream => _statusStream!;

  String get _wsUrl {
    final String base = baseUrl
        .replaceFirst(RegExp(r'^http://'), 'ws://')
        .replaceFirst(RegExp(r'^https://'), 'wss://');
    return base.endsWith('/') ? base.substring(0, base.length - 1) : base;
  }

  /// Establish a WebSocket connection for [runId], starting from [afterLine].
  /// Returns a broadcast stream of [WsEvent]s.
  Stream<WsEvent> connect(String runId, {int afterLine = 0}) {
    _lastReceivedLineIndex = afterLine;
    _disposed = false;
    _initialDisconnectTime = null;

    _eventController = StreamController<WsEvent>.broadcast();
    _statusController = StreamController<ConnectionStatus>.broadcast();
    _eventStream = _eventController!.stream;
    _statusStream = _statusController!.stream;

    _connectInternal(runId, attempt: 0);

    return _eventController!.stream;
  }

  Future<void> _connectInternal(String runId, {required int attempt}) async {
    if (_disposed) return;

    final String url = '$_wsUrl/api/v1/runs/$runId/stream';
    try {
      _socket = await WebSocket.connect(url);

      // Successful connection — clear disconnect timer
      _initialDisconnectTime = null;
      _statusController?.add(ConnectionStatus.connected);

      // Send auth frame first (before any data is read). Format matches the
      // backend's _auth_frame() helper used in test_websocket_contract.py.
      final String authFrame = jsonEncode(<String, dynamic>{
        'type': 'auth',
        'token': apiKey,
        'after_line': _lastReceivedLineIndex,
      });
      _socket!.add(authFrame);

      _socket!.listen(
        (dynamic data) {
          if (_disposed) return;
          if (data is String) {
            try {
              final Map<String, dynamic> decoded =
                  jsonDecode(data) as Map<String, dynamic>;
              final WsEvent event = WsEvent.fromJson(decoded);
              if (event.event == 'heartbeat') {
                // Heartbeat consumed — reset idle timer but NOT forwarded to
                // the caller's stream (AC-030).
                return;
              }
              _lastReceivedLineIndex++;
              _eventController?.add(event);
            } catch (_) {
              // Ignore malformed frames silently
            }
          }
        },
        onDone: () {
          if (!_disposed) {
            // Record initial disconnect time only on the FIRST drop
            _initialDisconnectTime ??= DateTime.now();
            _handleReconnect(runId, attempt: attempt + 1);
          }
        },
        onError: (Object error) {
          if (!_disposed) {
            _initialDisconnectTime ??= DateTime.now();
            _handleReconnect(runId, attempt: attempt + 1);
          }
        },
        cancelOnError: true,
      );
    } catch (_) {
      if (!_disposed) {
        _initialDisconnectTime ??= DateTime.now();
        _handleReconnect(runId, attempt: attempt + 1);
      }
    }
  }

  void _handleReconnect(String runId, {required int attempt}) {
    if (_disposed) return;
    _statusController?.add(ConnectionStatus.reconnecting);

    // Measure total elapsed time from the original disconnect (not each attempt).
    final DateTime disconnectedAt =
        _initialDisconnectTime ?? DateTime.now();
    final Duration elapsed = DateTime.now().difference(disconnectedAt);
    if (elapsed.inSeconds >= 30) {
      _statusController?.add(ConnectionStatus.polling);
      return;
    }

    // Exponential backoff sequence: 1s, 2s, 4s, 8s, 16s, 30s, 30s... (AC-020)
    const List<int> backoffSeconds = <int>[1, 2, 4, 8, 16, 30, 30];
    final int backoff =
        backoffSeconds[attempt.clamp(0, backoffSeconds.length - 1)];

    _reconnectTimer = Timer(Duration(seconds: backoff), () {
      if (_disposed) return;
      // Re-check total elapsed time after the backoff delay
      final Duration newElapsed =
          DateTime.now().difference(disconnectedAt);
      if (newElapsed.inSeconds >= 30) {
        _statusController?.add(ConnectionStatus.polling);
        return;
      }
      _connectInternal(runId, attempt: attempt);
    });
  }

  /// Cancel the connection and all associated streams.
  void disconnect() {
    _disposed = true;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _socket?.close();
    _socket = null;
    _initialDisconnectTime = null;
    _statusController?.add(ConnectionStatus.disconnected);
    _eventController?.close();
    _statusController?.close();
  }
}
