import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../models/ssh_credentials.dart';

class Credentials {
  final String url;
  final String apiKey;
  const Credentials({required this.url, required this.apiKey});
}

class SecureStorageService {
  static const _storage = FlutterSecureStorage();

  // ── API credential keys ───────────────────────────────────────────────────
  static const _urlKey = 'server_url';
  static const _apiKeyKey = 'api_key';

  // ── SSH credential keys ───────────────────────────────────────────────────
  static const _sshHostKey = 'ssh_host';
  static const _sshPortKey = 'ssh_port';
  static const _sshUsernameKey = 'ssh_username';
  static const _sshPasswordKey = 'ssh_password';
  static const _sshPrivateKeyKey = 'ssh_private_key';

  // ── API credentials ───────────────────────────────────────────────────────

  Future<void> saveCredentials(String url, String apiKey) async {
    await _storage.write(key: _urlKey, value: url);
    await _storage.write(key: _apiKeyKey, value: apiKey);
  }

  Future<Credentials?> loadCredentials() async {
    final url = await _storage.read(key: _urlKey);
    final apiKey = await _storage.read(key: _apiKeyKey);
    if (url == null || apiKey == null) return null;
    return Credentials(url: url, apiKey: apiKey);
  }

  Future<bool> hasCredentials() async {
    final creds = await loadCredentials();
    return creds != null;
  }

  Future<void> clear() async {
    await _storage.deleteAll();
  }

  // ── SSH credentials ───────────────────────────────────────────────────────

  /// Saves SSH connection parameters to SecureStorage.
  ///
  /// The [creds.password] and [creds.privateKeyPem] are sensitive — they are
  /// stored encrypted and must never be logged or printed.
  Future<void> saveSshCredentials(SshCredentials creds) async {
    await _storage.write(key: _sshHostKey, value: creds.host);
    await _storage.write(
        key: _sshPortKey, value: creds.port.toString());
    await _storage.write(key: _sshUsernameKey, value: creds.username);

    if (creds.password != null) {
      await _storage.write(key: _sshPasswordKey, value: creds.password);
    } else {
      await _storage.delete(key: _sshPasswordKey);
    }

    if (creds.privateKeyPem != null) {
      await _storage.write(
          key: _sshPrivateKeyKey, value: creds.privateKeyPem);
    } else {
      await _storage.delete(key: _sshPrivateKeyKey);
    }
  }

  /// Loads SSH credentials from SecureStorage.
  ///
  /// Returns null if no SSH host has been saved yet.
  Future<SshCredentials?> loadSshCredentials() async {
    final host = await _storage.read(key: _sshHostKey);
    if (host == null) return null;

    final portStr = await _storage.read(key: _sshPortKey);
    final port = int.tryParse(portStr ?? '') ?? 22;
    final username = await _storage.read(key: _sshUsernameKey) ?? '';

    // Sensitive fields — loaded but never logged
    final password = await _storage.read(key: _sshPasswordKey);
    final privateKeyPem = await _storage.read(key: _sshPrivateKeyKey);

    return SshCredentials(
      host: host,
      port: port,
      username: username,
      password: password,
      privateKeyPem: privateKeyPem,
    );
  }

  /// Removes all SSH-related keys without touching API credentials.
  Future<void> clearSshCredentials() async {
    await _storage.delete(key: _sshHostKey);
    await _storage.delete(key: _sshPortKey);
    await _storage.delete(key: _sshUsernameKey);
    await _storage.delete(key: _sshPasswordKey);
    await _storage.delete(key: _sshPrivateKeyKey);
  }
}
