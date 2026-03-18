// Basic smoke test - verifies the app widget can be instantiated.
// Full integration tests require a running backend server.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:orchestrator_mobile/app.dart';

void main() {
  setUp(() {
    // Configure flutter_secure_storage for tests — simulates first launch
    // with no stored credentials so GoRouter redirects to /settings.
    FlutterSecureStorage.setMockInitialValues({});
  });

  testWidgets('App starts without crashing', (WidgetTester tester) async {
    await tester.pumpWidget(
      const ProviderScope(child: OrchestratorApp()),
    );
    // App should start in loading state then redirect to settings
    await tester.pump();
    // Verify no uncaught exceptions
    expect(tester.takeException(), isNull);
  });

  testWidgets('App redirects to settings on first launch',
      (WidgetTester tester) async {
    FlutterSecureStorage.setMockInitialValues({});

    await tester.pumpWidget(
      const ProviderScope(child: OrchestratorApp()),
    );

    // Wait for auth provider to resolve (no credentials → AsyncData(null))
    // and GoRouter to redirect to /settings
    await tester.pumpAndSettle(const Duration(seconds: 2));

    // Should be on settings screen
    expect(find.text('Connect to Orchestrator'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('App shows dashboard when credentials are pre-saved',
      (WidgetTester tester) async {
    FlutterSecureStorage.setMockInitialValues({
      'server_url': 'http://100.64.0.1:8090',
      'api_key': 'test-api-key-123',
    });

    await tester.pumpWidget(
      const ProviderScope(child: OrchestratorApp()),
    );

    // Wait for auth provider → AsyncData(Credentials) → dashboard
    // The dashboard will show a loading indicator (no real backend)
    await tester.pumpAndSettle(const Duration(seconds: 2));

    // App bar title 'Orchestrator' indicates we're on the dashboard
    expect(find.text('Orchestrator'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
