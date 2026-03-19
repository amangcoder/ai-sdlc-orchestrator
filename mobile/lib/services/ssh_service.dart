import 'dart:async';
import 'dart:convert';
import 'package:dartssh2/dartssh2.dart';
import '../models/ssh_credentials.dart';

/// SSH connection status emitted on [SshService.statusStream].
enum SshConnectionStatus {
  connecting,
  connected,
  disconnected,
  error,
}

/// Manages a direct SSH session from the mobile device to the orchestrator
/// server using the dartssh2 package.
///
/// The SSH session is NOT proxied through the mobile API server — it connects
/// directly over TCP to [SshCredentials.host]:[SshCredentials.port].
class SshService {
  SSHClient? _client;
  SSHSession? _session;

  final _outputController = StreamController<String>.broadcast();
  final _statusController =
      StreamController<SshConnectionStatus>.broadcast();

  /// Raw terminal output from the SSH channel for display in the xterm widget.
  Stream<String> get outputStream => _outputController.stream;

  /// SSH connection lifecycle events.
  Stream<SshConnectionStatus> get statusStream => _statusController.stream;

  /// Whether an active SSH session exists.
  bool get isConnected => _session != null;

  /// Establishes an SSH session with a PTY of [cols]×[rows] dimensions.
  ///
  /// Enforces a minimum of 80×24.  Authenticates using password if
  /// [creds.password] is set, otherwise falls back to [creds.privateKeyPem].
  Future<void> connect(SshCredentials creds, int cols, int rows) async {
    // Enforce minimum PTY size
    final int effectiveCols = cols < 80 ? 80 : cols;
    final int effectiveRows = rows < 24 ? 24 : rows;

    _emitStatus(SshConnectionStatus.connecting);

    try {
      // Establish TCP + SSH handshake
      final socket = await SSHSocket.connect(creds.host, creds.port);

      SSHClient client;
      if (creds.privateKeyPem != null && creds.privateKeyPem!.isNotEmpty) {
        // Key-based authentication — never log the key content
        client = SSHClient(
          socket,
          username: creds.username,
          identities: [
            ...SSHKeyPair.fromPem(creds.privateKeyPem!),
          ],
        );
      } else {
        // Password authentication — never log the password
        final pwd = creds.password ?? '';
        client = SSHClient(
          socket,
          username: creds.username,
          onPasswordRequest: () => pwd,
        );
      }

      _client = client;

      // Wait for authentication to complete
      await client.authenticated;

      // Open a PTY shell session
      final session = await client.shell(
        pty: SSHPtyConfig(
          width: effectiveCols,
          height: effectiveRows,
        ),
      );

      _session = session;
      _emitStatus(SshConnectionStatus.connected);

      // Relay stdout to the output stream
      session.stdout
          .cast<List<int>>()
          .transform(const Utf8Decoder(allowMalformed: true))
          .listen(
        (data) {
          if (!_outputController.isClosed) {
            _outputController.add(data);
          }
        },
        onDone: _handleSessionClosed,
        onError: (_) => _handleSessionClosed(),
        cancelOnError: false,
      );

      // Relay stderr to the same output stream
      session.stderr
          .cast<List<int>>()
          .transform(const Utf8Decoder(allowMalformed: true))
          .listen(
        (data) {
          if (!_outputController.isClosed) {
            _outputController.add(data);
          }
        },
        cancelOnError: false,
      );
    } on SSHAuthFailError {
      _emitStatus(SshConnectionStatus.error);
      _outputController.add(
          '\r\n\x1B[31mAuthentication failed — check SSH credentials.\x1B[0m\r\n');
      rethrow;
    } catch (e) {
      _emitStatus(SshConnectionStatus.error);
      _outputController.add(
          '\r\n\x1B[31mConnection error: $e\x1B[0m\r\n');
      rethrow;
    }
  }

  /// Cleanly disconnects the SSH session and channel.
  void disconnect() {
    _session?.close();
    _client?.close();
    _session = null;
    _client = null;
    _emitStatus(SshConnectionStatus.disconnected);
  }

  /// Sends raw [data] to the SSH channel stdin.
  ///
  /// Special key mappings:
  ///   Ctrl+C → 0x03, Ctrl+Z → 0x1A (handled by the caller via xterm onOutput).
  void sendInput(String data) {
    final session = _session;
    if (session == null) return;
    session.stdin.add(utf8.encode(data));
  }

  /// Sends a PTY resize event to the server.
  ///
  /// Enforces the same 80×24 minimum as [connect].
  void resize(int cols, int rows) {
    final int effectiveCols = cols < 80 ? 80 : cols;
    final int effectiveRows = rows < 24 ? 24 : rows;
    _session?.resizeTerminal(effectiveCols, effectiveRows);
  }

  /// Disposes the streams. Call when the SSH terminal screen is permanently
  /// destroyed.
  void dispose() {
    disconnect();
    _outputController.close();
    _statusController.close();
  }

  void _emitStatus(SshConnectionStatus status) {
    if (!_statusController.isClosed) {
      _statusController.add(status);
    }
  }

  void _handleSessionClosed() {
    _session = null;
    _client = null;
    _emitStatus(SshConnectionStatus.disconnected);
  }
}
