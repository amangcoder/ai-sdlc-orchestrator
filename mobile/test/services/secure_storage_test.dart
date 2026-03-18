import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/services/secure_storage_service.dart';

void main() {
  group('Credentials', () {
    test('is immutable value object', () {
      const creds = Credentials(
        url: 'http://100.64.1.1:8090',
        apiKey: 'test-key-123',
      );

      expect(creds.url, 'http://100.64.1.1:8090');
      expect(creds.apiKey, 'test-key-123');
    });

    test('two instances with same values are equal by content', () {
      const creds1 = Credentials(url: 'http://test', apiKey: 'key');
      const creds2 = Credentials(url: 'http://test', apiKey: 'key');

      // Dart const objects with same values are identical
      expect(identical(creds1, creds2), true);
    });
  });
}
