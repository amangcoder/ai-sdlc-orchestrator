import 'package:flutter/material.dart';
import '../services/api_service.dart';

/// Maps typed exceptions to human-readable messages and optional actions.
class ErrorMessages {
  static ({String message, String? actionLabel, String? actionRoute})
      fromException(Object error) {
    if (error is AuthException) {
      return (
        message: 'Authentication failed — check your API key in Settings',
        actionLabel: 'Open Settings',
        actionRoute: '/settings',
      );
    }
    if (error is NotFoundException) {
      return (
        message: error.message,
        actionLabel: null,
        actionRoute: null,
      );
    }
    if (error is ConflictException) {
      return (
        message: 'Run already active',
        actionLabel: null,
        actionRoute: null,
      );
    }
    if (error is RateLimitException) {
      return (
        message: 'Too many requests — please wait a moment',
        actionLabel: null,
        actionRoute: null,
      );
    }
    if (error is ServerException) {
      return (
        message: 'Server error: ${error.message}',
        actionLabel: null,
        actionRoute: null,
      );
    }
    if (error is NetworkException) {
      return (
        message: 'Cannot reach server — check Tailscale is connected',
        actionLabel: null,
        actionRoute: null,
      );
    }
    return (
      message: error.toString(),
      actionLabel: null,
      actionRoute: null,
    );
  }
}

/// Centered error display with optional retry and action buttons.
class ErrorStateWidget extends StatelessWidget {
  final String message;
  final VoidCallback? onRetry;
  final String? actionLabel;
  final VoidCallback? onAction;

  const ErrorStateWidget({
    super.key,
    required this.message,
    this.onRetry,
    this.actionLabel,
    this.onAction,
  });

  factory ErrorStateWidget.fromException(
    Object error, {
    Key? key,
    VoidCallback? onRetry,
    void Function(String route)? onNavigate,
  }) {
    final mapped = ErrorMessages.fromException(error);
    return ErrorStateWidget(
      key: key,
      message: mapped.message,
      onRetry: onRetry,
      actionLabel: mapped.actionLabel,
      onAction: mapped.actionRoute != null && onNavigate != null
          ? () => onNavigate(mapped.actionRoute!)
          : null,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.error_outline,
              size: 48,
              color: Theme.of(context).colorScheme.error,
            ),
            const SizedBox(height: 16),
            Text(
              message,
              style: Theme.of(context).textTheme.bodyLarge,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            if (onRetry != null)
              ElevatedButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry'),
              ),
            if (actionLabel != null && onAction != null) ...[
              const SizedBox(height: 8),
              TextButton(
                onPressed: onAction,
                child: Text(actionLabel!),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
