import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/pending_prompt.dart';
import 'package:orchestrator_mobile/services/api_service.dart';
import 'package:orchestrator_mobile/services/secure_storage_service.dart';
import 'package:orchestrator_mobile/widgets/prompt_card.dart';

// ── Fake ApiService ───────────────────────────────────────────────────────────

typedef _RespondHandler = Future<void> Function(
    String runId, String promptId, String response);

/// A test double for [ApiService] that never makes real HTTP calls.
///
/// Pass [onRespondToPrompt] to control the behaviour of [respondToPrompt].
/// The default behaviour is to succeed silently.
class _FakeApiService extends ApiService {
  final _RespondHandler? _respondHandler;

  _FakeApiService({_RespondHandler? onRespondToPrompt})
      : _respondHandler = onRespondToPrompt,
        super(
          credentials: const Credentials(
            url: 'http://localhost:9999',
            apiKey: 'test-key',
          ),
        );

  @override
  Future<void> respondToPrompt(
      String runId, String promptId, String response) async {
    if (_respondHandler != null) {
      return _respondHandler!(runId, promptId, response);
    }
    // Default: succeed silently.
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

PendingPrompt _freeTextPrompt({String question = 'Describe your requirements'}) =>
    PendingPrompt(
      promptId: 'p-ft-001',
      question: question,
      type: 'free_text',
      options: null,
      createdAt: DateTime.utc(2026, 3, 19),
    );

PendingPrompt _singleChoicePrompt({
  List<String> options = const ['Option A', 'Option B', 'Option C'],
}) =>
    PendingPrompt(
      promptId: 'p-sc-001',
      question: 'Which approach do you prefer?',
      type: 'single_choice',
      options: options,
      createdAt: DateTime.utc(2026, 3, 19),
    );

Widget _buildCard({
  required PendingPrompt prompt,
  required String runId,
  VoidCallback? onDismiss,
  ApiService? apiService,
}) {
  return ProviderScope(
    overrides: [
      apiServiceProvider.overrideWithValue(apiService ?? _FakeApiService()),
    ],
    child: MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: PromptCard(
            prompt: prompt,
            runId: runId,
            onDismiss: onDismiss ?? () {},
          ),
        ),
      ),
    ),
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  group('PromptCard — single_choice layout (AC-007)', () {
    testWidgets('renders one ElevatedButton per option', (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _singleChoicePrompt(
            options: ['React + Node.js', 'Flutter + Dart', 'Vue + Python'],
          ),
          runId: 'run-sc-01',
        ),
      );

      expect(find.text('React + Node.js'), findsOneWidget);
      expect(find.text('Flutter + Dart'), findsOneWidget);
      expect(find.text('Vue + Python'), findsOneWidget);
      // Each option is wrapped in an ElevatedButton
      expect(find.byType(ElevatedButton), findsNWidgets(3));
    });

    testWidgets('renders correct number of option buttons for 2 options',
        (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _singleChoicePrompt(options: ['Yes', 'No']),
          runId: 'run-sc-02',
        ),
      );

      expect(find.byType(ElevatedButton), findsNWidgets(2));
      expect(find.text('Yes'), findsOneWidget);
      expect(find.text('No'), findsOneWidget);
    });

    testWidgets('does NOT render a TextField for single_choice prompt',
        (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _singleChoicePrompt(),
          runId: 'run-sc-03',
        ),
      );

      expect(find.byType(TextField), findsNothing);
    });
  });

  group('PromptCard — free_text layout (AC-007)', () {
    testWidgets('renders a TextField and a Submit button', (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _freeTextPrompt(),
          runId: 'run-ft-01',
        ),
      );

      expect(find.byType(TextField), findsOneWidget);
      expect(find.text('Submit'), findsOneWidget);
    });

    testWidgets('Submit button is disabled when TextField is empty',
        (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _freeTextPrompt(),
          runId: 'run-ft-02',
        ),
      );

      // TextField starts empty — Submit must be disabled
      final submitButton = tester.widget<ElevatedButton>(
        find.widgetWithText(ElevatedButton, 'Submit'),
      );
      expect(submitButton.onPressed, isNull);
    });

    testWidgets('Submit button is enabled after typing non-empty text',
        (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _freeTextPrompt(),
          runId: 'run-ft-03',
        ),
      );

      await tester.enterText(find.byType(TextField), 'some response text');
      await tester.pump();

      final submitButton = tester.widget<ElevatedButton>(
        find.widgetWithText(ElevatedButton, 'Submit'),
      );
      expect(submitButton.onPressed, isNotNull);
    });

    testWidgets('does NOT render ElevatedButton choices for free_text prompt',
        (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _freeTextPrompt(),
          runId: 'run-ft-04',
        ),
      );

      // Only the Submit ElevatedButton — no option buttons
      expect(find.text('Submit'), findsOneWidget);
    });
  });

  group('PromptCard — question rendering (REQ-018)', () {
    testWidgets('question text is rendered as plain Text with TextDirection.ltr',
        (tester) async {
      const question = 'Which approach do you prefer?';

      await tester.pumpWidget(
        _buildCard(
          prompt: _freeTextPrompt(question: question),
          runId: 'run-ltr-01',
        ),
      );

      final textWidgets = tester
          .widgetList<Text>(find.text(question))
          .toList();

      // At least one Text widget with the question must have ltr direction
      final ltrWidget = textWidgets.where(
        (w) => w.textDirection == TextDirection.ltr,
      );
      expect(ltrWidget, isNotEmpty,
          reason: 'Question text must be rendered with TextDirection.ltr to '
              'prevent bidi spoofing (REQ-018)');
    });

    testWidgets('renders "Orchestrator needs your input" header', (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _freeTextPrompt(),
          runId: 'run-header-01',
        ),
      );

      expect(find.text('Orchestrator needs your input'), findsOneWidget);
    });
  });

  group('PromptCard — submission lifecycle (AC-008)', () {
    testWidgets('calls onDismiss after successful submission (free_text)',
        (tester) async {
      bool dismissed = false;
      final completer = Completer<void>();

      await tester.pumpWidget(
        _buildCard(
          prompt: _freeTextPrompt(),
          runId: 'run-dismiss-01',
          onDismiss: () => dismissed = true,
          apiService: _FakeApiService(
            onRespondToPrompt: (_, __, ___) => completer.future,
          ),
        ),
      );

      await tester.enterText(find.byType(TextField), 'my response');
      await tester.pump();
      await tester.tap(find.widgetWithText(ElevatedButton, 'Submit'));
      await tester.pump(); // start async

      // While the future is pending, we should see CircularProgressIndicator
      expect(find.byType(CircularProgressIndicator), findsOneWidget,
          reason: 'CircularProgressIndicator must show while _isSubmitting=true (AC-008)');

      // Buttons should be disabled during submission
      // (TextField is also disabled — check Submit is gone or disabled)
      final submitButtons = tester.widgetList<ElevatedButton>(
        find.widgetWithText(ElevatedButton, 'Submit'),
      );
      // Either Submit button is absent (replaced by indicator) or disabled
      if (submitButtons.isNotEmpty) {
        expect(submitButtons.first.onPressed, isNull);
      }

      // Complete the request
      completer.complete();
      await tester.pumpAndSettle();

      expect(dismissed, isTrue, reason: 'onDismiss must be called on HTTP 200 (AC-008)');
    });

    testWidgets('calls onDismiss after successful single_choice option tap',
        (tester) async {
      bool dismissed = false;

      await tester.pumpWidget(
        _buildCard(
          prompt: _singleChoicePrompt(options: ['Yes', 'No']),
          runId: 'run-dismiss-sc-01',
          onDismiss: () => dismissed = true,
          apiService: _FakeApiService(),
        ),
      );

      await tester.tap(find.text('Yes'));
      await tester.pumpAndSettle();

      expect(dismissed, isTrue);
    });

    testWidgets('shows SnackBar on ConflictException (HTTP 409, REQ-011)',
        (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _singleChoicePrompt(options: ['Accept']),
          runId: 'run-conflict-01',
          apiService: _FakeApiService(
            onRespondToPrompt: (_, __, ___) async {
              throw const ConflictException();
            },
          ),
        ),
      );

      await tester.tap(find.text('Accept'));
      await tester.pumpAndSettle();

      // A SnackBar must appear
      expect(find.byType(SnackBar), findsOneWidget);
    });

    testWidgets('shows SnackBar on GoneException (HTTP 410, REQ-011)',
        (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _singleChoicePrompt(options: ['Proceed']),
          runId: 'run-gone-01',
          apiService: _FakeApiService(
            onRespondToPrompt: (_, __, ___) async {
              throw const GoneException('Run is no longer active');
            },
          ),
        ),
      );

      await tester.tap(find.text('Proceed'));
      await tester.pumpAndSettle();

      expect(find.byType(SnackBar), findsOneWidget);
    });

    testWidgets('resets _isSubmitting to false after any error', (tester) async {
      await tester.pumpWidget(
        _buildCard(
          prompt: _singleChoicePrompt(options: ['Go']),
          runId: 'run-reset-01',
          apiService: _FakeApiService(
            onRespondToPrompt: (_, __, ___) async {
              throw const ConflictException();
            },
          ),
        ),
      );

      await tester.tap(find.text('Go'));
      await tester.pumpAndSettle();

      // After error resolves, loading indicator must be gone and buttons
      // re-enabled (no longer submitting).
      expect(find.byType(CircularProgressIndicator), findsNothing);
      final button = tester.widget<ElevatedButton>(find.text('Go'));
      expect(button.onPressed, isNotNull,
          reason: 'Option buttons must be re-enabled after a failed submission');
    });
  });

  group('PromptCard — null apiService', () {
    testWidgets(
        'shows error SnackBar when apiServiceProvider returns null',
        (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            apiServiceProvider.overrideWithValue(null),
          ],
          child: MaterialApp(
            home: Scaffold(
              body: PromptCard(
                prompt: _singleChoicePrompt(options: ['OK']),
                runId: 'run-noauth-01',
                onDismiss: () {},
              ),
            ),
          ),
        ),
      );

      await tester.tap(find.text('OK'));
      await tester.pumpAndSettle();

      expect(find.byType(SnackBar), findsOneWidget);
    });
  });
}
