import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class Credentials {
  final String url;
  final String apiKey;
  const Credentials({required this.url, required this.apiKey});
}

class SecureStorageService {
  static const _storage = FlutterSecureStorage();
  static const _urlKey = 'server_url';
  static const _apiKeyKey = 'api_key';

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
}
