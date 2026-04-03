import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/slo_report_model.dart';
import '../services/api_service.dart';
import 'auth_provider.dart';

/// Async notifier that fetches and caches the SLO compliance report.
class SloNotifier extends AsyncNotifier<SloReport?> {
  @override
  Future<SloReport?> build() async {
    return _fetch();
  }

  Future<SloReport?> _fetch() async {
    final creds = await ref.read(authNotifierProvider.future);
    if (creds == null) return null;
    final api = ref.read(apiServiceProvider) ?? ApiService(credentials: creds);
    try {
      return await api.getSloReport();
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

/// Global provider for the SLO report.
final sloNotifierProvider =
    AsyncNotifierProvider<SloNotifier, SloReport?>(() => SloNotifier());
