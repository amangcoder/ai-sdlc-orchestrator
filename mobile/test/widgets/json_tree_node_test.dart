import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/widgets/json_tree_node.dart';

void main() {
  group('JsonTreeNode', () {
    testWidgets('renders collapsed Map summary', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'config',
                value: {'a': 1, 'b': 2, 'c': 3},
                depth: 3, // deep enough to start collapsed
                initiallyExpanded: false,
              ),
            ),
          ),
        ),
      );

      // When collapsed, should show "{ N keys }" summary
      expect(find.text('{ 3 keys }'), findsOneWidget);
      expect(find.text('config: '), findsOneWidget);
    });

    testWidgets('renders collapsed List summary', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'items',
                value: [1, 2, 3, 4, 5],
                depth: 3,
                initiallyExpanded: false,
              ),
            ),
          ),
        ),
      );

      expect(find.text('[ 5 items ]'), findsOneWidget);
    });

    testWidgets('renders single item correctly', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'items',
                value: ['only'],
                depth: 3,
                initiallyExpanded: false,
              ),
            ),
          ),
        ),
      );

      expect(find.text('[ 1 item ]'), findsOneWidget);
    });

    testWidgets('renders single key correctly', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'obj',
                value: const {'key': 'value'},
                depth: 3,
                initiallyExpanded: false,
              ),
            ),
          ),
        ),
      );

      expect(find.text('{ 1 key }'), findsOneWidget);
    });

    testWidgets('renders leaf string in green', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'name',
                value: 'hello world',
                depth: 0,
              ),
            ),
          ),
        ),
      );

      expect(find.text('"hello world"'), findsOneWidget);
    });

    testWidgets('renders null as grey italic', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'error',
                value: null,
                depth: 0,
              ),
            ),
          ),
        ),
      );

      expect(find.text('null'), findsOneWidget);
    });

    testWidgets('renders number in blue', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'count',
                value: 42,
                depth: 0,
              ),
            ),
          ),
        ),
      );

      expect(find.text('42'), findsOneWidget);
    });

    testWidgets('renders boolean in orange', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'enabled',
                value: true,
                depth: 0,
              ),
            ),
          ),
        ),
      );

      expect(find.text('true'), findsOneWidget);
    });

    testWidgets('toggles expansion on tap', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: JsonTreeNode(
                label: 'data',
                value: {'key': 'value'},
                depth: 0,
                initiallyExpanded: true,
              ),
            ),
          ),
        ),
      );

      // Initially expanded, children visible
      expect(find.text('"value"'), findsOneWidget);

      // Tap to collapse
      await tester.tap(find.text('data: '));
      await tester.pump();

      // After collapse, should show summary
      expect(find.text('{ 1 key }'), findsOneWidget);
    });
  });

  group('EditableJsonTreeNode', () {
    testWidgets('shows lock icon for redacted values', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: EditableJsonTreeNode(
                label: 'api_key',
                value: '***REDACTED***',
                depth: 0,
                topLevelKey: 'api_key',
              ),
            ),
          ),
        ),
      );

      expect(find.byIcon(Icons.lock_outline), findsOneWidget);
      expect(find.text('***REDACTED***'), findsOneWidget);
    });

    testWidgets('non-redacted string is tappable for editing',
        (tester) async {
      String? changedKey;
      dynamic changedValue;

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: EditableJsonTreeNode(
                label: 'name',
                value: 'old-value',
                depth: 1,
                topLevelKey: 'project',
                onChanged: (key, value) {
                  changedKey = key;
                  changedValue = value;
                },
              ),
            ),
          ),
        ),
      );

      // Tap the value to start editing
      await tester.tap(find.text('"old-value"'));
      await tester.pump();

      // Should now show a text field
      expect(find.byType(TextField), findsOneWidget);

      // Clear and type new value
      await tester.enterText(find.byType(TextField), 'new-value');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      expect(changedKey, 'project');
      expect(changedValue, 'new-value');
    });
  });
}
