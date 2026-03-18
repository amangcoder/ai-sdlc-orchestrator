import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/widgets/phase_list_item.dart';
import 'package:orchestrator_mobile/models/phase_state.dart';

void main() {
  group('PhaseListItem', () {
    testWidgets('shows formatted phase name', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'user_psychology_research',
              phase: const PhaseState(status: 'completed'),
            ),
          ),
        ),
      );

      expect(find.text('User Psychology Research'), findsOneWidget);
    });

    testWidgets('shows green check for completed status', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'prd',
              phase: const PhaseState(status: 'completed', costUsd: 1.50),
            ),
          ),
        ),
      );

      expect(find.byIcon(Icons.check_circle), findsOneWidget);
    });

    testWidgets('shows spinner for running status', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'architecture',
              phase: const PhaseState(status: 'running'),
            ),
          ),
        ),
      );

      expect(find.byType(CircularProgressIndicator), findsOneWidget);
    });

    testWidgets('shows red X for failed status', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'qa',
              phase: const PhaseState(
                status: 'failed',
                error: 'Agent timeout',
              ),
            ),
          ),
        ),
      );

      expect(find.byIcon(Icons.cancel), findsOneWidget);
    });

    testWidgets('shows error text for failed phase', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'qa',
              phase: const PhaseState(
                status: 'failed',
                error: 'Agent timeout after 300 seconds',
              ),
            ),
          ),
        ),
      );

      expect(find.text('Agent timeout after 300 seconds'), findsOneWidget);
    });

    testWidgets('clips long error text to 80 chars', (tester) async {
      final longError = 'A' * 100;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'qa',
              phase: PhaseState(
                status: 'failed',
                error: longError,
              ),
            ),
          ),
        ),
      );

      // Should show truncated error (first 80 chars + ellipsis)
      final truncated = '${longError.substring(0, 80)}…';
      expect(find.textContaining(truncated), findsOneWidget);
    });

    testWidgets('shows cost when greater than zero', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'prd',
              phase: const PhaseState(
                status: 'completed',
                costUsd: 2.3456,
              ),
            ),
          ),
        ),
      );

      expect(find.text('\$2.3456'), findsOneWidget);
    });

    testWidgets('hides cost when null', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'prd',
              phase: const PhaseState(status: 'completed'),
            ),
          ),
        ),
      );

      // Should not find any dollar amount
      expect(find.textContaining('\$'), findsNothing);
    });

    testWidgets('shows model tier when available', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'prd',
              phase: const PhaseState(
                status: 'completed',
                modelTier: 'premium',
              ),
            ),
          ),
        ),
      );

      expect(find.text('premium'), findsOneWidget);
    });

    testWidgets('shows grey clock for pending status', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'code_review',
              phase: const PhaseState(status: 'pending'),
            ),
          ),
        ),
      );

      expect(find.byIcon(Icons.schedule), findsOneWidget);
    });

    testWidgets('shows grey dash for skipped status', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PhaseListItem(
              phaseName: 'debate',
              phase: const PhaseState(status: 'skipped'),
            ),
          ),
        ),
      );

      expect(find.byIcon(Icons.remove), findsOneWidget);
    });
  });
}
