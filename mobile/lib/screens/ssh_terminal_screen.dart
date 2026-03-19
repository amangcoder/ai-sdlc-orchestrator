import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:xterm/xterm.dart';
import '../services/ssh_service.dart';
import '../services/secure_storage_service.dart';

/// Full-screen SSH terminal using xterm for ANSI rendering and dartssh2 for
/// the SSH transport layer.
///
/// On open, loads [SshCredentials] from [SecureStorageService].  If none are
/// configured the screen shows a setup prompt instead of the terminal.
///
/// The xterm scroll buffer is preserved across disconnect/reconnect cycles
/// because the same [Terminal] instance is reused.
///
/// Enhancements (TASK-005):
///   - Per-session in-memory command history (max 50 unique entries).
///   - Mobile input bar with up/down chevron buttons for history cycling.
///   - Full-width connection status banner with reconnect affordance.
class SshTerminalScreen extends ConsumerStatefulWidget {
  const SshTerminalScreen({super.key});

  @override
  ConsumerState<SshTerminalScreen> createState() => _SshTerminalScreenState();
}

class _SshTerminalScreenState extends ConsumerState<SshTerminalScreen> {
  final _storage = SecureStorageService();
  final _sshService = SshService();
  late final Terminal _terminal;

  SshConnectionStatus _status = SshConnectionStatus.connecting;
  bool _credentialsLoaded = false;
  bool _hasCredentials = false;

  StreamSubscription<String>? _outputSub;
  StreamSubscription<SshConnectionStatus>? _statusSub;

  // ── Command history (TASK-005) ────────────────────────────────────────────

  /// Capped at 50 unique entries; most-recent is at the tail.
  final List<String> _commandHistory = [];

  /// -1 means "at current input" (not navigating history).
  int _historyIndex = -1;

  final TextEditingController _inputController = TextEditingController();
  final FocusNode _inputFocusNode = FocusNode();

  // ─────────────────────────────────────────────────────────────────────────

  @override
  void initState() {
    super.initState();
    _terminal = Terminal(
      maxLines: 10000,
      onOutput: (data) {
        _sshService.sendInput(data);
      },
      onResize: (cols, rows, pixelWidth, pixelHeight) {
        _sshService.resize(cols, rows);
      },
    );
    _subscribeToService();
    _loadAndConnect();
  }

  void _subscribeToService() {
    _outputSub = _sshService.outputStream.listen((data) {
      if (mounted) {
        _terminal.write(data);
      }
    });

    _statusSub = _sshService.statusStream.listen((status) {
      if (mounted) {
        setState(() => _status = status);
      }
    });
  }

  Future<void> _loadAndConnect() async {
    final creds = await _storage.loadSshCredentials();
    if (!mounted) return;

    if (creds == null) {
      setState(() {
        _credentialsLoaded = true;
        _hasCredentials = false;
      });
      return;
    }

    setState(() {
      _credentialsLoaded = true;
      _hasCredentials = true;
    });

    // Use default 80×24 PTY size on initial connect; resize events will follow
    // once the TerminalView is laid out.
    await _connect(creds);
  }

  Future<void> _connect(dynamic creds) async {
    try {
      await _sshService.connect(
        creds,
        _terminal.viewWidth > 0 ? _terminal.viewWidth : 80,
        _terminal.viewHeight > 0 ? _terminal.viewHeight : 24,
      );
    } catch (e) {
      if (mounted) {
        _terminal.write(
            '\r\n\x1B[31mFailed to connect: $e\x1B[0m\r\n');
      }
    }
  }

  Future<void> _reconnect() async {
    final creds = await _storage.loadSshCredentials();
    if (!mounted || creds == null) return;
    _sshService.disconnect();
    await _connect(creds);
  }

  // ── Command history helpers ───────────────────────────────────────────────

  /// Submits a command from the mobile input bar.
  ///
  /// Adds to history (max 50, no consecutive duplicates), sends to SSH, and
  /// clears the input field.
  void _onCommandSubmit(String cmd) {
    final trimmed = cmd.trim();
    if (trimmed.isEmpty) return;

    // Skip if identical to the last entry (no consecutive duplicates).
    if (_commandHistory.isEmpty || _commandHistory.last != trimmed) {
      _commandHistory.add(trimmed);
      // Cap at 50: drop the oldest when over the limit.
      if (_commandHistory.length > 50) {
        _commandHistory.removeAt(0);
      }
    }
    // Reset navigation index to "current input".
    _historyIndex = -1;

    // Send command to the SSH session (newline = Enter).
    _sshService.sendInput('$trimmed\n');

    // Clear the input field.
    _inputController.clear();
    setState(() {});
  }

  /// Moves backward through history (older commands).
  void _historyUp() {
    if (_commandHistory.isEmpty) return;
    setState(() {
      if (_historyIndex == -1) {
        _historyIndex = _commandHistory.length - 1;
      } else if (_historyIndex > 0) {
        _historyIndex -= 1;
      }
      _inputController.text = _commandHistory[_historyIndex];
      _inputController.selection =
          TextSelection.collapsed(offset: _inputController.text.length);
    });
  }

  /// Moves forward through history (newer commands → clears at -1).
  void _historyDown() {
    if (_commandHistory.isEmpty || _historyIndex == -1) return;
    setState(() {
      if (_historyIndex < _commandHistory.length - 1) {
        _historyIndex += 1;
        _inputController.text = _commandHistory[_historyIndex];
      } else {
        // Past the most-recent entry → clear input.
        _historyIndex = -1;
        _inputController.clear();
      }
      _inputController.selection =
          TextSelection.collapsed(offset: _inputController.text.length);
    });
  }

  // ─────────────────────────────────────────────────────────────────────────

  @override
  void dispose() {
    _outputSub?.cancel();
    _statusSub?.cancel();
    _sshService.dispose();
    _inputController.dispose();
    _inputFocusNode.dispose();
    // Clear command history on dispose — in-memory only, not persisted.
    _commandHistory.clear();
    super.dispose();
  }

  Color _statusColor(SshConnectionStatus status) {
    switch (status) {
      case SshConnectionStatus.connecting:
        return Colors.amber.shade700;
      case SshConnectionStatus.connected:
        return Colors.green.shade600;
      case SshConnectionStatus.disconnected:
      case SshConnectionStatus.error:
        return Colors.red.shade600;
    }
  }

  String _statusLabel(SshConnectionStatus status) {
    switch (status) {
      case SshConnectionStatus.connecting:
        return 'Reconnecting…';
      case SshConnectionStatus.connected:
        return 'Connected';
      case SshConnectionStatus.disconnected:
        return 'Disconnected';
      case SshConnectionStatus.error:
        return 'Error';
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: const Text('SSH Terminal',
            style: TextStyle(color: Colors.white)),
        actions: [
          if (_hasCredentials)
            IconButton(
              icon: const Icon(Icons.refresh, color: Colors.white),
              tooltip: 'Reconnect',
              onPressed: _reconnect,
            ),
        ],
      ),
      body: !_credentialsLoaded
          ? const Center(
              child: CircularProgressIndicator(color: Colors.white),
            )
          : !_hasCredentials
              ? _buildNoCredentialsPrompt(theme)
              : _buildTerminal(theme),
    );
  }

  Widget _buildNoCredentialsPrompt(ThemeData theme) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.terminal,
                size: 64, color: Colors.white54),
            const SizedBox(height: 24),
            const Text(
              'SSH not configured',
              style: TextStyle(
                  color: Colors.white,
                  fontSize: 20,
                  fontWeight: FontWeight.bold),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 12),
            const Text(
              'Configure SSH credentials in Settings to use the terminal.',
              style: TextStyle(color: Colors.white70),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 24),
            ElevatedButton.icon(
              onPressed: () => context.push('/settings'),
              icon: const Icon(Icons.settings),
              label: const Text('Go to Settings'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTerminal(ThemeData theme) {
    final isDisconnected = _status == SshConnectionStatus.disconnected ||
        _status == SshConnectionStatus.error;

    return KeyboardListener(
      focusNode: FocusNode(),
      onKeyEvent: (KeyEvent event) {
        // Physical keyboard arrow-key history navigation (desktop/physical KB).
        if (event is KeyDownEvent) {
          if (event.logicalKey == LogicalKeyboardKey.arrowUp) {
            _historyUp();
          } else if (event.logicalKey == LogicalKeyboardKey.arrowDown) {
            _historyDown();
          }
        }
      },
      child: Column(
        children: [
          // ── Full-width connection status banner (AC-014) ──────────────
          Container(
            width: double.infinity,
            color: _statusColor(_status).withOpacity(0.15),
            padding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: Row(
              children: [
                Container(
                  width: 10,
                  height: 10,
                  decoration: BoxDecoration(
                    color: _statusColor(_status),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  _statusLabel(_status),
                  style: TextStyle(
                    color: _statusColor(_status),
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),

          // ── Disconnected retry banner (only when no active retry) ─────
          if (isDisconnected)
            Container(
              width: double.infinity,
              color: Colors.red.shade900,
              padding: const EdgeInsets.symmetric(
                  horizontal: 16, vertical: 8),
              child: Row(
                children: [
                  const Expanded(
                    child: Text(
                      'Connection lost — tap Retry to reconnect',
                      style: TextStyle(color: Colors.white),
                    ),
                  ),
                  TextButton(
                    onPressed: _reconnect,
                    child: const Text(
                      'Retry',
                      style: TextStyle(color: Colors.white),
                    ),
                  ),
                ],
              ),
            ),

          // ── xterm terminal view ────────────────────────────────────────
          Expanded(
            child: TerminalView(
              _terminal,
              autofocus: true,
            ),
          ),

          // ── Mobile command input bar with history (AC-013) ────────────
          Container(
            color: Colors.grey.shade900,
            padding:
                const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            child: Row(
              children: [
                // History-up chevron (previous command)
                Semantics(
                  label: 'Previous command',
                  button: true,
                  child: IconButton(
                    icon: const Icon(Icons.keyboard_arrow_up,
                        color: Colors.white70, size: 22),
                    tooltip: 'Previous command',
                    visualDensity: VisualDensity.compact,
                    onPressed: _commandHistory.isNotEmpty
                        ? _historyUp
                        : null,
                  ),
                ),
                // History-down chevron (next / clear)
                Semantics(
                  label: 'Next command',
                  button: true,
                  child: IconButton(
                    icon: const Icon(Icons.keyboard_arrow_down,
                        color: Colors.white70, size: 22),
                    tooltip: 'Next command',
                    visualDensity: VisualDensity.compact,
                    onPressed: (_commandHistory.isNotEmpty &&
                            _historyIndex != -1)
                        ? _historyDown
                        : null,
                  ),
                ),
                // Command text field
                Expanded(
                  child: TextField(
                    controller: _inputController,
                    focusNode: _inputFocusNode,
                    style: const TextStyle(
                        color: Colors.white,
                        fontFamily: 'monospace',
                        fontSize: 14),
                    decoration: InputDecoration(
                      hintText: 'Command…',
                      hintStyle:
                          const TextStyle(color: Colors.white38),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(6),
                        borderSide:
                            const BorderSide(color: Colors.white24),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(6),
                        borderSide:
                            const BorderSide(color: Colors.white24),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(6),
                        borderSide:
                            const BorderSide(color: Colors.white54),
                      ),
                      contentPadding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 8),
                      isDense: true,
                    ),
                    cursorColor: Colors.white,
                    textInputAction: TextInputAction.send,
                    onSubmitted: _onCommandSubmit,
                    onChanged: (_) => setState(() {}),
                  ),
                ),
                const SizedBox(width: 4),
                // Send button
                Semantics(
                  label: 'Send command',
                  button: true,
                  child: IconButton(
                    icon: const Icon(Icons.send, color: Colors.white70),
                    tooltip: 'Send command',
                    visualDensity: VisualDensity.compact,
                    onPressed: _inputController.text.isNotEmpty
                        ? () => _onCommandSubmit(_inputController.text)
                        : null,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
