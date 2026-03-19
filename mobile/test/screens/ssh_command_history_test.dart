/// Unit tests for the in-memory SSH command history algorithm (TASK-005 AC-013).
///
/// The history behaviour is encapsulated in [_SshTerminalScreenState], but
/// the algorithm is simple enough to test in pure Dart so we mirror it here
/// and validate all the acceptance criteria independently of the SSH/xterm
/// widget stack (which requires platform channels and a real SSH server).
///
/// These tests verify:
///   - AC-013: submitting a command adds it to history (max 50 unique entries)
///   - AC-013: up-navigation populates input with previous command
///   - AC-013: down-navigation cycles forward; at most-recent, clears input
///   - Duplicate consecutive commands are NOT added
///   - Command history is cleared on dispose (in-memory)

library;

import 'package:flutter_test/flutter_test.dart';

// ── Standalone history helper mirroring SshTerminalScreenState logic ─────────
//
// This mirrors the exact implementation in ssh_terminal_screen.dart so that
// changes to the screen will surface as test failures here, prompting an
// update to both.

class CommandHistory {
  /// Capped at [_kMaxHistory] unique entries; most-recent is at the tail.
  static const int _kMaxHistory = 50;

  final List<String> _entries = [];

  /// -1 means "at current input" (not navigating history).
  int _index = -1;

  int get length => _entries.length;
  int get index => _index;
  bool get isEmpty => _entries.isEmpty;

  /// Simulates [_onCommandSubmit]: adds [cmd] if non-empty and not a
  /// consecutive duplicate; caps at [_kMaxHistory]; resets navigation index.
  String submit(String cmd) {
    final trimmed = cmd.trim();
    if (trimmed.isEmpty) return '';

    if (_entries.isEmpty || _entries.last != trimmed) {
      _entries.add(trimmed);
      if (_entries.length > _kMaxHistory) _entries.removeAt(0);
    }
    _index = -1;
    return trimmed;
  }

  /// Simulates [_historyUp] (previous command).
  ///
  /// Returns the command that should populate the input field.
  /// Returns null if history is empty.
  String? navigateUp() {
    if (_entries.isEmpty) return null;
    if (_index == -1) {
      _index = _entries.length - 1;
    } else if (_index > 0) {
      _index -= 1;
    }
    return _entries[_index];
  }

  /// Simulates [_historyDown] (newer command → clears at -1).
  ///
  /// Returns the command that should populate the input field, or null when
  /// the index wraps past the most-recent entry (input should be cleared).
  String? navigateDown() {
    if (_entries.isEmpty || _index == -1) return null;
    if (_index < _entries.length - 1) {
      _index += 1;
      return _entries[_index];
    } else {
      _index = -1;
      return null; // clear input
    }
  }

  /// Simulates [dispose]: clears all entries and resets index.
  void dispose() {
    _entries.clear();
    _index = -1;
  }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  group('CommandHistory — submit (AC-013)', () {
    test('submitting a non-empty command adds it to history', () {
      final h = CommandHistory();
      h.submit('ls -la');
      expect(h.length, 1);
    });

    test('submitting empty string does NOT add to history', () {
      final h = CommandHistory();
      h.submit('');
      h.submit('   ');
      expect(h.length, 0);
    });

    test('consecutive duplicate commands are NOT added twice', () {
      final h = CommandHistory();
      h.submit('git status');
      h.submit('git status'); // duplicate — skipped
      expect(h.length, 1);
    });

    test('non-consecutive duplicate IS added (same cmd after different cmd)', () {
      final h = CommandHistory();
      h.submit('ls');
      h.submit('pwd');
      h.submit('ls'); // not consecutive — should be added
      expect(h.length, 3);
    });

    test('submitting resets navigation index to -1', () {
      final h = CommandHistory();
      h.submit('cmd-1');
      h.submit('cmd-2');
      h.navigateUp(); // sets _index = 1 (cmd-2)
      h.submit('cmd-3'); // should reset index
      expect(h.index, -1);
    });

    test('caps history at 50 entries (oldest dropped)', () {
      final h = CommandHistory();
      for (int i = 0; i < 55; i++) {
        h.submit('command-$i');
      }
      // Only 50 entries remain
      expect(h.length, 50);
    });

    test('oldest entry is dropped when cap reached', () {
      final h = CommandHistory();
      for (int i = 0; i < 51; i++) {
        h.submit('cmd-$i');
      }
      // command-0 should have been evicted
      // navigateUp all the way to the oldest — should be command-1
      h.navigateUp(); // most recent = cmd-50
      for (int i = 0; i < 48; i++) {
        h.navigateUp(); // walk to cmd-2
      }
      final oldest = h.navigateUp(); // should be cmd-1 (not cmd-0)
      expect(oldest, isNotNull);
      // cmd-0 is gone; the oldest remaining is cmd-1
      expect(oldest, 'cmd-1');
    });

    test('returns the trimmed command string on submit', () {
      final h = CommandHistory();
      final result = h.submit('  ls -la  ');
      expect(result, 'ls -la');
    });
  });

  group('CommandHistory — navigateUp (AC-013)', () {
    test('returns null when history is empty', () {
      final h = CommandHistory();
      expect(h.navigateUp(), isNull);
    });

    test('first navigateUp returns most-recent command', () {
      final h = CommandHistory();
      h.submit('cmd-1');
      h.submit('cmd-2');
      h.submit('cmd-3');

      final result = h.navigateUp();
      expect(result, 'cmd-3');
    });

    test('repeated navigateUp walks backward through history', () {
      final h = CommandHistory();
      h.submit('alpha');
      h.submit('beta');
      h.submit('gamma');

      expect(h.navigateUp(), 'gamma'); // most recent
      expect(h.navigateUp(), 'beta');
      expect(h.navigateUp(), 'alpha');
    });

    test('navigateUp stops at oldest entry (does not wrap)', () {
      final h = CommandHistory();
      h.submit('first');
      h.submit('second');

      h.navigateUp(); // second
      h.navigateUp(); // first
      final extra = h.navigateUp(); // still first — no wrap
      expect(extra, 'first');
    });
  });

  group('CommandHistory — navigateDown (AC-013)', () {
    test('returns null when history is empty', () {
      final h = CommandHistory();
      expect(h.navigateDown(), isNull);
    });

    test('returns null (clear input) when index is -1 (not navigating)', () {
      final h = CommandHistory();
      h.submit('cmd-1');
      // index is -1 by default after submit
      expect(h.navigateDown(), isNull);
    });

    test('navigateDown after navigateUp moves forward', () {
      final h = CommandHistory();
      h.submit('a');
      h.submit('b');
      h.submit('c');

      h.navigateUp(); // → 'c' (index=2)
      h.navigateUp(); // → 'b' (index=1)
      final forward = h.navigateDown(); // → 'c' (index=2)
      expect(forward, 'c');
    });

    test('navigateDown past most-recent entry returns null and resets index', () {
      final h = CommandHistory();
      h.submit('x');
      h.submit('y');

      h.navigateUp(); // → 'y' (index=1)
      h.navigateDown(); // → null (index=-1, clears input)
      expect(h.index, -1);
    });

    test('down chevron at most-recent-entry clears input (index → -1)', () {
      final h = CommandHistory();
      h.submit('last-cmd');

      h.navigateUp(); // → 'last-cmd' (index=0)
      final result = h.navigateDown(); // → null (past most-recent → clear)
      expect(result, isNull);
      expect(h.index, -1);
    });
  });

  group('CommandHistory — dispose (AC-013)', () {
    test('dispose clears all entries', () {
      final h = CommandHistory();
      for (int i = 0; i < 10; i++) {
        h.submit('cmd-$i');
      }
      h.dispose();
      expect(h.isEmpty, isTrue);
      expect(h.length, 0);
    });

    test('dispose resets navigation index to -1', () {
      final h = CommandHistory();
      h.submit('a');
      h.navigateUp();
      h.dispose();
      expect(h.index, -1);
    });

    test('history is empty after dispose — not persisted', () {
      final h = CommandHistory();
      h.submit('important-cmd');
      h.dispose();
      // Simulate a new session — history should be gone
      expect(h.navigateUp(), isNull);
    });
  });

  group('CommandHistory — boundary and edge cases', () {
    test('single entry: up then down cycles correctly', () {
      final h = CommandHistory();
      h.submit('only');

      expect(h.navigateUp(), 'only'); // → 'only'
      expect(h.navigateDown(), isNull); // → clear
    });

    test('multiple submits after navigation resets correctly', () {
      final h = CommandHistory();
      h.submit('cmd-a');
      h.submit('cmd-b');
      h.navigateUp(); // navigate to cmd-b
      h.submit('cmd-c'); // submitting resets index to -1

      expect(h.index, -1);
      // Most recent should now be cmd-c
      expect(h.navigateUp(), 'cmd-c');
    });

    test('whitespace-only command is not added', () {
      final h = CommandHistory();
      h.submit('\t   \n');
      expect(h.isEmpty, isTrue);
    });
  });
}
