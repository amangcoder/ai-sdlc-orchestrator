import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/alert_model.dart';
import '../services/api_service.dart';
import 'auth_provider.dart';

/// Async notifier that fetches and caches the alerts list.
class AlertsNotifier extends AsyncNotifier<List<Alert>> {
  @override
  Future<List<Alert>> build() async {
    return _fetch();
  }

  Future<List<Alert>> _fetch() async {
    final creds = await ref.read(authNotifierProvider.future);
    if (creds == null) return [];
    final api = ref.read(apiServiceProvider) ?? ApiService(credentials: creds);
    try {
      return await api.getAlerts();
    } on AuthException {
      await ref.read(authNotifierProvider.notifier).clear();
      return [];
    }
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetch);
  }
}

/// Global provider for the alerts list.
final alertsNotifierProvider =
    AsyncNotifierProvider<AlertsNotifier, List<Alert>>(
        () => AlertsNotifier());
