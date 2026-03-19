import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/ssh_credentials.dart';
import 'package:orchestrator_mobile/services/secure_storage_service.dart';

void main() {
  // ── SshCredentials model ────────────────────────────────────────────────

  group('SshCredentials model', () {
    test('constructor sets all required fields', () {
      const creds = SshCredentials(
        host: '10.0.0.1',
        port: 22,
        username: 'alice',
      );

      expect(creds.host, '10.0.0.1');
      expect(creds.port, 22);
      expect(creds.username, 'alice');
      expect(creds.password, isNull);
      expect(creds.privateKeyPem, isNull);
    });

    test('constructor stores optional password and privateKeyPem', () {
      const creds = SshCredentials(
        host: '10.0.0.1',
        port: 22,
        username: 'alice',
        password: 's3cret',
        privateKeyPem: '-----BEGIN RSA PRIVATE KEY-----\n...',
      );

      expect(creds.password, 's3cret');
      expect(creds.privateKeyPem, '-----BEGIN RSA PRIVATE KEY-----\n...');
    });

    test('copyWith replaces only the specified fields', () {
      const creds = SshCredentials(
        host: '10.0.0.1',
        port: 22,
        username: 'alice',
        password: 's3cret',
      );

      final updated = creds.copyWith(host: '192.168.0.1', port: 2222);

      expect(updated.host, '192.168.0.1');
      expect(updated.port, 2222);
      expect(updated.username, 'alice'); // unchanged
      expect(updated.password, 's3cret'); // unchanged
    });

    test('copyWith preserves privateKeyPem when not provided', () {
      const creds = SshCredentials(
        host: '10.0.0.1',
        port: 22,
        username: 'bob',
        privateKeyPem: '-----BEGIN OPENSSH PRIVATE KEY-----\n...',
      );

      final updated = creds.copyWith(username: 'charlie');

      expect(updated.privateKeyPem,
          '-----BEGIN OPENSSH PRIVATE KEY-----\n...');
    });

    test('copyWith with no arguments preserves all values', () {
      const creds = SshCredentials(
        host: '10.0.0.1',
        port: 2222,
        username: 'bob',
        password: 'pass',
        privateKeyPem: 'pem-data',
      );

      final copy = creds.copyWith();

      expect(copy.host, creds.host);
      expect(copy.port, creds.port);
      expect(copy.username, creds.username);
      expect(copy.password, creds.password);
      expect(copy.privateKeyPem, creds.privateKeyPem);
    });

    test('supports non-standard SSH port', () {
      const creds = SshCredentials(
        host: 'bastion.example.com',
        port: 2222,
        username: 'deploy',
      );

      expect(creds.port, 2222);
    });

    test('password and privateKeyPem are independent nullable fields', () {
      // Only password set
      const onlyPassword = SshCredentials(
        host: 'host',
        port: 22,
        username: 'user',
        password: 'pwd',
      );
      expect(onlyPassword.password, isNotNull);
      expect(onlyPassword.privateKeyPem, isNull);

      // Only key set
      const onlyKey = SshCredentials(
        host: 'host',
        port: 22,
        username: 'user',
        privateKeyPem: 'pem',
      );
      expect(onlyKey.password, isNull);
      expect(onlyKey.privateKeyPem, isNotNull);
    });
  });

  // ── SecureStorageService structural checks ──────────────────────────────
  //
  // Full round-trip tests (save → load → clear) require a real platform
  // channel for FlutterSecureStorage, which is not available in unit tests
  // without a mock.  The following tests verify that the service class is
  // properly instantiable and expose the expected API surface.

  group('SecureStorageService', () {
    test('can be instantiated without errors', () {
      expect(() => SecureStorageService(), returnsNormally);
    });

    test('instance exposes saveSshCredentials method', () {
      final service = SecureStorageService();
      expect(service.saveSshCredentials, isA<Function>());
    });

    test('instance exposes loadSshCredentials method', () {
      final service = SecureStorageService();
      expect(service.loadSshCredentials, isA<Function>());
    });

    test('instance exposes clearSshCredentials method', () {
      final service = SecureStorageService();
      expect(service.clearSshCredentials, isA<Function>());
    });

    test('instance exposes loadCredentials method (API creds unaffected)', () {
      final service = SecureStorageService();
      expect(service.loadCredentials, isA<Function>());
    });
  });

  // ── Credentials (API) model ─────────────────────────────────────────────

  group('Credentials model', () {
    test('stores url and apiKey', () {
      const creds = Credentials(url: 'http://10.0.0.1:8090', apiKey: 'tok');

      expect(creds.url, 'http://10.0.0.1:8090');
      expect(creds.apiKey, 'tok');
    });

    test('const instances with same values are identical', () {
      const a = Credentials(url: 'http://host', apiKey: 'key');
      const b = Credentials(url: 'http://host', apiKey: 'key');

      expect(identical(a, b), true);
    });
  });
}
