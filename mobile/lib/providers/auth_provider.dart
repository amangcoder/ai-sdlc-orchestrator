import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../services/secure_storage_service.dart';

/// Riverpod async notifier that manages authentication credentials.
/// Persists to Android Keystore via SecureStorageService.
class AuthNotifier extends AsyncNotifier<Credentials?> {
  final _storageService = SecureStorageService();

  @override
  Future<Credentials?> build() async {
    return _storageService.loadCredentials();
  }

  /// Save new credentials and update state immediately.
  Future<void> save(String url, String apiKey) async {
    await _storageService.saveCredentials(url, apiKey);
    state = AsyncData(Credentials(url: url, apiKey: apiKey));
  }

  /// Clear credentials (logout).
  Future<void> clear() async {
    await _storageService.clear();
    state = const AsyncData(null);
  }
}

/// Global provider for authentication state.
final authNotifierProvider =
    AsyncNotifierProvider<AuthNotifier, Credentials?>(() => AuthNotifier());
