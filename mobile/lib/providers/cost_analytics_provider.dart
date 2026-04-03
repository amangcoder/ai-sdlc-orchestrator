import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/cost_analytics_model.dart';
import '../services/api_service.dart';
import 'auth_provider.dart';

/// Async notifier that fetches and caches cost analytics data.
class CostAnalyticsNotifier extends AsyncNotifier<CostAnalytics?> {
  @override
  Future<CostAnalytics?> build() async {
    return _fetch();
  }

  Future<CostAnalytics?> _fetch() async {
    final creds = await ref.read(authNotifierProvider.future);
    if (creds == null) return null;
    final api = ref.read(apiServiceProvider) ?? ApiService(credentials: creds);
    try {
      return await api.getCostAnalytics();
    } on AuthException {
      await ref.read(authNotifierProvider.notifier).clear();
      return null;
    }
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetch);
  }
}

/// Global provider for cost analytics.
final costAnalyticsNotifierProvider =
    AsyncNotifierProvider<CostAnalyticsNotifier, CostAnalytics?>(
        () => CostAnalyticsNotifier());
