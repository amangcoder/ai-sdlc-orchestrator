import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/pending_prompt.dart';

void main() {
  group('PendingPrompt.fromJson', () {
    test('parses free_text prompt with null options', () {
      final json = {
        'prompt_id': 'prompt-uuid-001',
        'question': 'What is the target deployment environment?',
        'type': 'free_text',
        'options': null,
        'created_at': '2026-03-19T10:00:00.000Z',
      };

      final prompt = PendingPrompt.fromJson(json);

      expect(prompt.promptId, 'prompt-uuid-001');
      expect(prompt.question, 'What is the target deployment environment?');
      expect(prompt.type, 'free_text');
      expect(prompt.options, isNull);
      expect(prompt.createdAt, DateTime.parse('2026-03-19T10:00:00.000Z'));
    });

    test('parses single_choice prompt with non-null options list', () {
      final json = {
        'prompt_id': 'prompt-uuid-002',
        'question': 'Which tech stack should we use?',
        'type': 'single_choice',
        'options': ['React + Node.js', 'Flutter + Dart', 'Vue + Python'],
        'created_at': '2026-03-19T11:00:00.000Z',
      };

      final prompt = PendingPrompt.fromJson(json);

      expect(prompt.type, 'single_choice');
      expect(prompt.options, isNotNull);
      expect(prompt.options!.length, 3);
      expect(prompt.options, contains('React + Node.js'));
      expect(prompt.options, contains('Flutter + Dart'));
      expect(prompt.options, contains('Vue + Python'));
    });

    test('handles absent options field as null', () {
      final json = {
        'prompt_id': 'p3',
        'question': 'Describe the issue',
        'type': 'free_text',
        'created_at': '2026-03-19T12:00:00Z',
      };

      final prompt = PendingPrompt.fromJson(json);
      expect(prompt.options, isNull);
    });

    test('handles missing prompt_id gracefully with empty string', () {
      final json = {
        'question': 'Can you confirm?',
        'type': 'free_text',
        'created_at': '2026-03-19T12:00:00Z',
      };

      final prompt = PendingPrompt.fromJson(json);
      expect(prompt.promptId, '');
    });

    test('handles missing question gracefully with empty string', () {
      final json = {
        'prompt_id': 'p5',
        'type': 'single_choice',
        'options': ['Yes', 'No'],
        'created_at': '2026-03-19T12:00:00Z',
      };

      final prompt = PendingPrompt.fromJson(json);
      expect(prompt.question, '');
    });

    test('handles missing type with default free_text', () {
      final json = {
        'prompt_id': 'p6',
        'question': 'What now?',
        'created_at': '2026-03-19T12:00:00Z',
      };

      final prompt = PendingPrompt.fromJson(json);
      expect(prompt.type, 'free_text');
    });

    test('handles missing created_at by using current time (no throw)', () {
      final before = DateTime.now();
      final json = {
        'prompt_id': 'p7',
        'question': 'Any notes?',
        'type': 'free_text',
      };

      final prompt = PendingPrompt.fromJson(json);
      final after = DateTime.now();

      // Should not throw; createdAt defaults to approximately now
      expect(prompt.createdAt.isAfter(before.subtract(const Duration(seconds: 1))), isTrue);
      expect(prompt.createdAt.isBefore(after.add(const Duration(seconds: 1))), isTrue);
    });

    test('options list coerced to List<String> even if elements are non-string', () {
      final json = {
        'prompt_id': 'p8',
        'question': 'Pick a number',
        'type': 'single_choice',
        'options': [1, 2, 3],
        'created_at': '2026-03-19T12:00:00Z',
      };

      final prompt = PendingPrompt.fromJson(json);
      expect(prompt.options, isNotNull);
      expect(prompt.options!.every((e) => e is String), isTrue);
      expect(prompt.options, ['1', '2', '3']);
    });

    test('toJson round-trips all fields', () {
      final original = PendingPrompt(
        promptId: 'rt-1',
        question: 'Round-trip question?',
        type: 'single_choice',
        options: ['A', 'B'],
        createdAt: DateTime.utc(2026, 3, 19, 9, 0),
      );

      final json = original.toJson();
      final restored = PendingPrompt.fromJson(json);

      expect(restored.promptId, original.promptId);
      expect(restored.question, original.question);
      expect(restored.type, original.type);
      expect(restored.options, original.options);
      expect(
        restored.createdAt.millisecondsSinceEpoch,
        original.createdAt.millisecondsSinceEpoch,
      );
    });
  });
}
