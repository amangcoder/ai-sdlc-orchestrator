import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/widgets/run_card.dart';
import 'package:orchestrator_mobile/models/run_summary.dart';

void main() {
  group('RunCard', () {
    Widget buildCard(RunSummary run) {
      return MaterialApp(
        home: Scaffold(
          body: RunCard(run: run),
        ),
      );
    }

    testWidgets('shows truncated run_id prefix', (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc123def456',
          featureRequest: 'Test feature',
          workflowType: 'bugfix',
          status: 'completed',
        ),
      ));

      expect(find.text('abc123de'), findsOneWidget);
    });

    testWidgets('shows full run_id when shorter than 8 chars',
        (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc',
          featureRequest: 'Test',
          workflowType: 'bugfix',
          status: 'completed',
        ),
      ));

      expect(find.text('abc'), findsOneWidget);
    });

    testWidgets('shows formatted workflow type as chip', (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc123def456',
          featureRequest: 'Test',
          workflowType: 'feature_development',
          status: 'completed',
        ),
      ));

      expect(find.text('Feature Development'), findsOneWidget);
    });

    testWidgets('shows "Running:" prefix with current step', (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc123def456',
          featureRequest: 'Test',
          workflowType: 'bugfix',
          status: 'running',
          currentStep: 'architecture',
          startTime: null,
        ),
      ));

      expect(find.text('Running: architecture'), findsOneWidget);
    });

    testWidgets('shows cost formatted as dollar amount', (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc123def456',
          featureRequest: 'Test',
          workflowType: 'bugfix',
          status: 'completed',
          totalCostUsd: 12.34,
        ),
      ));

      expect(find.text('\$12.34'), findsOneWidget);
    });

    testWidgets('hides cost when null', (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc123def456',
          featureRequest: 'Test',
          workflowType: 'bugfix',
          status: 'completed',
        ),
      ));

      expect(find.textContaining('\$'), findsNothing);
    });

    testWidgets('shows feature request text', (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc123def456',
          featureRequest: 'Build a dashboard with real-time monitoring',
          workflowType: 'feature_development',
          status: 'running',
        ),
      ));

      expect(
        find.text('Build a dashboard with real-time monitoring'),
        findsOneWidget,
      );
    });

    testWidgets('renders status text', (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc123def456',
          featureRequest: 'Test',
          workflowType: 'bugfix',
          status: 'completed',
        ),
      ));

      expect(find.text('Completed'), findsOneWidget);
    });

    testWidgets('renders failed status text', (tester) async {
      await tester.pumpWidget(buildCard(
        const RunSummary(
          runId: 'abc123def456',
          featureRequest: 'Test',
          workflowType: 'bugfix',
          status: 'failed',
        ),
      ));

      expect(find.text('Failed'), findsOneWidget);
    });
  });
}
