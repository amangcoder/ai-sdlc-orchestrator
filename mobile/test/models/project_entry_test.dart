import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/project_entry.dart';

void main() {
  group('ProjectEntry.fromJson', () {
    test('parses all four fields from JSON', () {
      final json = {
        'id': 'abc123',
        'name': 'my-project',
        'last_modified': '2026-03-19T10:30:00.000Z',
        'run_count': 5,
      };

      final entry = ProjectEntry.fromJson(json);

      expect(entry.id, 'abc123');
      expect(entry.name, 'my-project');
      expect(entry.runCount, 5);
      expect(entry.lastModified, DateTime.parse('2026-03-19T10:30:00.000Z'));
    });

    test('defaults run_count to 0 when absent', () {
      final json = {
        'id': 'xyz',
        'name': 'project-no-runs',
        'last_modified': '2026-01-01T00:00:00Z',
      };

      final entry = ProjectEntry.fromJson(json);
      expect(entry.runCount, 0);
    });

    test('defaults run_count to 0 when null', () {
      final json = {
        'id': 'xyz',
        'name': 'project-null-runs',
        'last_modified': '2026-01-01T00:00:00Z',
        'run_count': null,
      };

      final entry = ProjectEntry.fromJson(json);
      expect(entry.runCount, 0);
    });

    test('parses ISO-8601 last_modified as UTC DateTime', () {
      final json = {
        'id': 'a',
        'name': 'b',
        'last_modified': '2026-03-15T08:00:00.000Z',
        'run_count': 1,
      };

      final entry = ProjectEntry.fromJson(json);
      expect(entry.lastModified.isUtc, isTrue);
      expect(entry.lastModified.year, 2026);
      expect(entry.lastModified.month, 3);
      expect(entry.lastModified.day, 15);
    });

    test('does not include a path field — path disclosure protection', () {
      final json = {
        'id': 'abc',
        'name': 'myproject',
        'last_modified': '2026-03-01T00:00:00Z',
        'run_count': 2,
      };

      final entry = ProjectEntry.fromJson(json);
      final serialized = entry.toJson();

      expect(serialized.containsKey('path'), isFalse);
      expect(serialized.keys.toSet(), {'id', 'name', 'last_modified', 'run_count'});
    });

    test('toJson round-trips all fields correctly', () {
      final original = ProjectEntry(
        id: 'round-trip-id',
        name: 'round-trip',
        lastModified: DateTime.utc(2026, 3, 19, 12, 0),
        runCount: 7,
      );

      final json = original.toJson();
      final restored = ProjectEntry.fromJson(json);

      expect(restored.id, original.id);
      expect(restored.name, original.name);
      expect(restored.runCount, original.runCount);
      // DateTime may lose sub-millisecond precision through ISO-8601
      expect(
        restored.lastModified.millisecondsSinceEpoch,
        original.lastModified.millisecondsSinceEpoch,
      );
    });

    test('handles run_count of zero explicitly', () {
      final json = {
        'id': 'empty-proj',
        'name': 'empty',
        'last_modified': '2026-03-01T00:00:00Z',
        'run_count': 0,
      };

      final entry = ProjectEntry.fromJson(json);
      expect(entry.runCount, 0);
    });
  });
}
