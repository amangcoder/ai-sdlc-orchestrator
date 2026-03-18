import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

/// Persists per-directory flag configurations and the last-selected directory
/// using SharedPreferences (non-encrypted plaintext store).
///
/// Deliberately NOT using SecureStorageService — that is reserved for API
/// credentials.
class FlagPreferencesService {
  static const _kLastDirKey = 'last_selected_dir_id';
  static const _kFlagsPrefix = 'flags_';

  Future<String?> loadLastDirectoryId() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getString(_kLastDirKey);
    } catch (_) {
      return null;
    }
  }

  Future<void> saveLastDirectoryId(String id) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_kLastDirKey, id);
    } catch (_) {}
  }

  Future<Map<String, dynamic>?> loadFlags(String dirId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString('$_kFlagsPrefix$dirId');
      if (raw == null) return null;
      return jsonDecode(raw) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }

  Future<void> saveFlags(String dirId, Map<String, dynamic> flags) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('$_kFlagsPrefix$dirId', jsonEncode(flags));
    } catch (_) {}
  }

  Future<void> clearFlags(String dirId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove('$_kFlagsPrefix$dirId');
    } catch (_) {}
  }
}
