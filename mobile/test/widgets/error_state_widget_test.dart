import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/services/api_service.dart';
import 'package:orchestrator_mobile/widgets/error_state_widget.dart';

void main() {
  group('ErrorMessages.fromException', () {
    test('maps AuthException to settings message', () {
      final result = ErrorMessages.fromException(const AuthException());
      expect(result.message,
          'Authentication failed — check your API key in Settings');
      expect(result.actionLabel, 'Open Settings');
      expect(result.actionRoute, '/settings');
    });

    test('maps NotFoundException to its message', () {
      final result = ErrorMessages.fromException(
          const NotFoundException('Run not found'));
      expect(result.message, 'Run not found');
      expect(result.actionLabel, isNull);
    });

    test('maps ConflictException', () {
      final result = ErrorMessages.fromException(
          const ConflictException(activeRunId: 'abc123'));
      expect(result.message, 'Run already active');
    });

    test('maps RateLimitException', () {
      final result =
          ErrorMessages.fromException(const RateLimitException());
      expect(result.message,
          'Too many requests — please wait a moment');
    });

    test('maps ServerException with message', () {
      final result = ErrorMessages.fromException(
          const ServerException('Internal server error'));
      expect(result.message, 'Server error: Internal server error');
    });

    test('maps NetworkException to Tailscale message', () {
      final result =
          ErrorMessages.fromException(const NetworkException());
      expect(result.message,
          'Cannot reach server — check Tailscale is connected');
    });

    test('maps unknown exception to toString', () {
      final result =
          ErrorMessages.fromException(Exception('weird error'));
      expect(result.message, contains('weird error'));
    });
  });

  group('ErrorStateWidget', () {
    testWidgets('shows error icon and message', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ErrorStateWidget(message: 'Something went wrong'),
          ),
        ),
      );

      expect(find.byIcon(Icons.error_outline), findsOneWidget);
      expect(find.text('Something went wrong'), findsOneWidget);
    });

    testWidgets('shows retry button when onRetry provided', (tester) async {
      bool retried = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ErrorStateWidget(
              message: 'Error',
              onRetry: () => retried = true,
            ),
          ),
        ),
      );

      expect(find.text('Retry'), findsOneWidget);
      await tester.tap(find.text('Retry'));
      expect(retried, true);
    });

    testWidgets('hides retry button when onRetry is null', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ErrorStateWidget(message: 'Error'),
          ),
        ),
      );

      expect(find.text('Retry'), findsNothing);
    });

    testWidgets('shows action button when provided', (tester) async {
      bool actionTapped = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ErrorStateWidget(
              message: 'Auth failed',
              actionLabel: 'Open Settings',
              onAction: () => actionTapped = true,
            ),
          ),
        ),
      );

      expect(find.text('Open Settings'), findsOneWidget);
      await tester.tap(find.text('Open Settings'));
      expect(actionTapped, true);
    });

    testWidgets('fromException creates widget from AuthException',
        (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ErrorStateWidget.fromException(
              const AuthException(),
              onRetry: () {},
              onNavigate: (_) {},
            ),
          ),
        ),
      );

      expect(
        find.text(
            'Authentication failed — check your API key in Settings'),
        findsOneWidget,
      );
      expect(find.text('Open Settings'), findsOneWidget);
    });
  });
}
