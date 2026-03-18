import 'package:flutter/material.dart';
import '../services/websocket_service.dart';

/// Compact chip showing WebSocket connection status.
/// Only displayed for active runs.
class ConnectionStatusChip extends StatelessWidget {
  final ConnectionStatus status;

  const ConnectionStatusChip({super.key, required this.status});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: _dotColor,
          ),
        ),
        const SizedBox(width: 4),
        Text(
          _label,
          style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: _textColor(context),
              ),
        ),
      ],
    );
  }

  Color get _dotColor {
    switch (status) {
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

  Color _textColor(BuildContext context) {
    switch (status) {
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

  String get _label {
    switch (status) {
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
}
