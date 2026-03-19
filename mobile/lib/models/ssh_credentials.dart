/// SSH connection credentials stored exclusively in Flutter SecureStorage.
///
/// Never transmitted to the mobile API server — used only by [SshService]
/// for direct device-to-server SSH connections.
///
/// [password] and [privateKeyPem] are mutually optional; at least one should
/// be set for authentication to succeed. The private key PEM and password
/// are never logged or printed anywhere in the app.
class SshCredentials {
  final String host;
  final int port;
  final String username;

  /// Plain-text password for password-based auth. Never displayed in the UI.
  final String? password;

  /// PEM-encoded private key for public-key auth. Never displayed in the UI.
  final String? privateKeyPem;

  const SshCredentials({
    required this.host,
    required this.port,
    required this.username,
    this.password,
    this.privateKeyPem,
  });

  /// Returns a copy with updated fields.
  SshCredentials copyWith({
    String? host,
    int? port,
    String? username,
    String? password,
    String? privateKeyPem,
  }) =>
      SshCredentials(
        host: host ?? this.host,
        port: port ?? this.port,
        username: username ?? this.username,
        password: password ?? this.password,
        privateKeyPem: privateKeyPem ?? this.privateKeyPem,
      );
}
