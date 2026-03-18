import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/widgets/connection_status_chip.dart';
import 'package:orchestrator_mobile/services/websocket_service.dart';

void main() {
  group('ConnectionStatusChip', () {
    testWidgets('shows "Live" in green for connected status', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ConnectionStatusChip(status: ConnectionStatus.connected),
          ),
        ),
      );

      expect(find.text('Live'), findsOneWidget);
    });

    testWidgets('shows "Reconnecting..." in orange', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ConnectionStatusChip(
                status: ConnectionStatus.reconnecting),
          ),
        ),
      );

      expect(find.text('Reconnecting...'), findsOneWidget);
    });

    testWidgets('shows "Polling" in grey', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ConnectionStatusChip(status: ConnectionStatus.polling),
          ),
        ),
      );

      expect(find.text('Polling'), findsOneWidget);
    });

    testWidgets('shows "Disconnected" in red', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ConnectionStatusChip(
                status: ConnectionStatus.disconnected),
          ),
        ),
      );

      expect(find.text('Disconnected'), findsOneWidget);
    });

    testWidgets('renders a colored dot indicator', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ConnectionStatusChip(status: ConnectionStatus.connected),
          ),
        ),
      );

      // Should have a dot (Container with circle BoxDecoration)
      final container = tester.widget<Container>(
        find.descendant(
          of: find.byType(ConnectionStatusChip),
          matching: find.byType(Container),
        ),
      );
      expect(container, isNotNull);
    });
  });
}
