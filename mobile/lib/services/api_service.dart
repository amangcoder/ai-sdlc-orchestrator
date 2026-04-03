import 'dart:io';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/run_summary.dart';
import '../models/run_detail.dart';
import '../models/config_model.dart';
import '../models/directory_entry.dart';
import '../models/directory_children_response.dart';
import '../models/ssh_config_response.dart';
import '../models/project_entry.dart';
import '../models/pending_prompt.dart';
import '../models/cost_analytics_model.dart';
import '../models/slo_report_model.dart';
import '../models/alert_model.dart';
import '../models/artifact_search_result_model.dart';
import '../services/secure_storage_service.dart';
import '../providers/auth_provider.dart';

// Typed exceptions
class AuthException implements Exception {
  final String message;
  const AuthException([this.message = 'Authentication failed']);
}

class NotFoundException implements Exception {
  final String message;
  const NotFoundException([this.message = 'Not found']);
}

class ConflictException implements Exception {
  final String? activeRunId;
  const ConflictException({this.activeRunId});
}

class RateLimitException implements Exception {
  const RateLimitException();
}

/// Thrown when the run is no longer active (HTTP 410).
class GoneException implements Exception {
  final String? message;
  const GoneException([this.message]);

  @override
  String toString() => 'GoneException: ${message ?? 'Run is no longer active'}';
}

class ServerException implements Exception {
  final String message;
  const ServerException([this.message = 'Server error']);
}

class NetworkException implements Exception {
  final String message;
  const NetworkException([this.message = 'Network error']);
}

/// Thrown when a directory browse request is blocked by the server-side
/// max_browse_depth limit (HTTP 400 with a depth-limit error body).
class DirectoryDepthException implements Exception {
  final String message;
  const DirectoryDepthException(
      [this.message = 'Maximum directory depth reached']);
}

class ApiService {
  late final Dio _dio;
  final Credentials credentials;

  ApiService({required this.credentials}) {
    _dio = Dio(BaseOptions(
      baseUrl: credentials.url,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
      headers: <String, dynamic>{
        'Authorization': 'Bearer ${credentials.apiKey}',
        'Content-Type': 'application/json',
      },
    ));

    _dio.interceptors.add(InterceptorsWrapper(
      onError: (DioException error, ErrorInterceptorHandler handler) {
        if (error.type == DioExceptionType.connectionTimeout ||
            error.type == DioExceptionType.receiveTimeout ||
            error.type == DioExceptionType.connectionError) {
          handler.reject(DioException(
            requestOptions: error.requestOptions,
            error: const NetworkException(
                'Cannot reach server — check Tailscale is connected'),
            type: error.type,
          ));
          return;
        }
        if (error.response != null) {
          final int? statusCode = error.response!.statusCode;
          final dynamic responseData = error.response!.data;
          if (statusCode == 401) {
            handler.reject(DioException(
              requestOptions: error.requestOptions,
              error: const AuthException(),
              response: error.response,
              type: DioExceptionType.badResponse,
            ));
            return;
          } else if (statusCode == 404) {
            final String msg;
            if (responseData is Map<String, dynamic>) {
              msg = (responseData['error'] as String?) ?? 'Not found';
            } else {
              msg = 'Not found';
            }
            handler.reject(DioException(
              requestOptions: error.requestOptions,
              error: NotFoundException(msg),
              response: error.response,
              type: DioExceptionType.badResponse,
            ));
            return;
          } else if (statusCode == 409) {
            final String? activeRunId;
            if (responseData is Map<String, dynamic>) {
              activeRunId = responseData['active_run_id'] as String?;
            } else {
              activeRunId = null;
            }
            handler.reject(DioException(
              requestOptions: error.requestOptions,
              error: ConflictException(activeRunId: activeRunId),
              response: error.response,
              type: DioExceptionType.badResponse,
            ));
            return;
          } else if (statusCode == 410) {
            final String? msg;
            if (responseData is Map<String, dynamic>) {
              msg = (responseData['detail'] as String?) ??
                  (responseData['error'] as String?);
            } else {
              msg = null;
            }
            handler.reject(DioException(
              requestOptions: error.requestOptions,
              error: GoneException(msg),
              response: error.response,
              type: DioExceptionType.badResponse,
            ));
            return;
          } else if (statusCode == 429) {
            handler.reject(DioException(
              requestOptions: error.requestOptions,
              error: const RateLimitException(),
              response: error.response,
              type: DioExceptionType.badResponse,
            ));
            return;
          } else if (statusCode == 422) {
            final String msg;
            if (responseData is Map<String, dynamic>) {
              msg = (responseData['error'] as String?) ??
                  (responseData['detail'] as String?) ??
                  'Unprocessable entity';
            } else {
              msg = 'Unprocessable entity';
            }
            handler.reject(DioException(
              requestOptions: error.requestOptions,
              error: ServerException(msg),
              response: error.response,
              type: DioExceptionType.badResponse,
            ));
            return;
          } else if (statusCode != null && statusCode >= 500) {
            final String msg;
            if (responseData is Map<String, dynamic>) {
              msg = (responseData['detail'] as String?) ??
                  (responseData['error'] as String?) ??
                  'Server error';
            } else {
              msg = 'Server error';
            }
            handler.reject(DioException(
              requestOptions: error.requestOptions,
              error: ServerException(msg),
              response: error.response,
              type: DioExceptionType.badResponse,
            ));
            return;
          }
        }
        handler.next(error);
      },
    ));
  }

  Exception _mapError(DioException e) {
    if (e.error is AuthException) return e.error! as AuthException;
    if (e.error is NotFoundException) return e.error! as NotFoundException;
    if (e.error is ConflictException) return e.error! as ConflictException;
    if (e.error is GoneException) return e.error! as GoneException;
    if (e.error is RateLimitException) return e.error! as RateLimitException;
    if (e.error is ServerException) return e.error! as ServerException;
    if (e.error is NetworkException) return e.error! as NetworkException;
    if (e.error is SocketException) {
      return const NetworkException(
          'Cannot reach server — check Tailscale is connected');
    }
    return NetworkException(e.message ?? 'Network error');
  }

  Future<List<RunSummary>> listRuns() async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/runs');
      final List<dynamic> list = response.data as List<dynamic>;
      return list
          .map(
              (dynamic e) => RunSummary.fromJson(e as Map<String, dynamic>))
          .toList();
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<RunDetail> getRun(String runId) async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/runs/$runId');
      return RunDetail.fromJson(response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<Map<String, dynamic>> startRun(Map<String, dynamic> request) async {
    try {
      final Response<dynamic> response =
          await _dio.post<dynamic>('/api/v1/runs', data: request);
      return response.data as Map<String, dynamic>;
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<bool> cancelRun(String runId) async {
    try {
      final Response<dynamic> response =
          await _dio.post<dynamic>('/api/v1/runs/$runId/cancel');
      final Map<String, dynamic> data =
          response.data as Map<String, dynamic>;
      return data['cancelled'] as bool? ?? false;
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<Map<String, dynamic>> resumeRun(String runId) async {
    try {
      final Response<dynamic> response =
          await _dio.post<dynamic>('/api/v1/runs/$runId/resume');
      return response.data as Map<String, dynamic>;
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<List<String>> listArtifacts(String runId) async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/runs/$runId/artifacts');
      return (response.data as List<dynamic>).cast<String>();
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<Map<String, dynamic>> getArtifact(String runId, String name) async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/runs/$runId/artifacts/$name');
      return response.data as Map<String, dynamic>;
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<Map<String, dynamic>> getEvents(String runId,
      {int offset = 0}) async {
    try {
      final Response<dynamic> response = await _dio.get<dynamic>(
        '/api/v1/runs/$runId/events',
        queryParameters: <String, dynamic>{'offset': offset, 'limit': 100},
      );
      return response.data as Map<String, dynamic>;
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<ConfigModel> getConfig() async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/config');
      return ConfigModel.fromJson(response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<List<DirectoryEntry>> getDirectories() async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/directories');
      final Map<String, dynamic> data =
          response.data as Map<String, dynamic>;
      final List<dynamic> list = data['directories'] as List<dynamic>;
      return list
          .map((dynamic e) =>
              DirectoryEntry.fromJson(e as Map<String, dynamic>))
          .toList();
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<Map<String, dynamic>> updateConfig(
      Map<String, dynamic> changes) async {
    try {
      final Response<dynamic> response =
          await _dio.put<dynamic>('/api/v1/config', data: changes);
      return response.data as Map<String, dynamic>;
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  Future<Map<String, dynamic>> getHealth() async {
    // Health endpoint doesn't require auth
    final Dio noAuthDio = Dio(BaseOptions(
      baseUrl: credentials.url,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 10),
    ));
    try {
      final Response<dynamic> response =
          await noAuthDio.get<dynamic>('/health');
      return response.data as Map<String, dynamic>;
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  // ── Dynamic Directory Browse API ─────────────────────────────────────────

  /// Returns the root DirectoryEntry for the server's configured projects_root.
  ///
  /// Throws [NotFoundException] when projects_root is not configured (HTTP 404).
  Future<DirectoryEntry> getDirectoryRoot() async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/directories/root');
      return DirectoryEntry.fromJson(response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  /// Lists immediate children of the directory identified by [dirId].
  ///
  /// Throws [DirectoryDepthException] on HTTP 400 (depth limit reached).
  /// Throws [AuthException] on HTTP 403 (outside projects_root).
  Future<DirectoryChildrenResponse> getDirectoryChildren(String dirId) async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/directories/$dirId/children');
      return DirectoryChildrenResponse.fromJson(
          response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      // HTTP 400 may be a depth-limit error — inspect the body
      if (e.response?.statusCode == 400) {
        final dynamic data = e.response?.data;
        final String msg;
        if (data is Map<String, dynamic>) {
          msg = (data['detail'] as String?) ??
              (data['error'] as String?) ??
              'Maximum directory depth reached';
        } else {
          msg = 'Maximum directory depth reached';
        }
        throw DirectoryDepthException(msg);
      }
      throw _mapError(e);
    }
  }

  /// Creates a new subdirectory named [name] under the directory [parentDirId].
  ///
  /// Returns the newly created [DirectoryEntry].
  /// Throws [ConflictException] on HTTP 409 (directory already exists).
  /// Throws [ServerException] on HTTP 400 (invalid name regex).
  /// Throws [AuthException] on HTTP 403 (parent outside projects_root).
  Future<DirectoryEntry> createDirectory(
      String parentDirId, String name) async {
    try {
      final Response<dynamic> response = await _dio.post<dynamic>(
        '/api/v1/directories/$parentDirId/children',
        data: <String, dynamic>{'name': name},
      );
      return DirectoryEntry.fromJson(response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  // ── SSH Config Probe ──────────────────────────────────────────────────────

  /// Returns the SSH configuration status from the server (no auth required).
  ///
  /// Never throws on a reachability failure — the response body reflects it.
  Future<SshConfigResponse> getSshConfig() async {
    // SSH config endpoint does not require authentication
    final Dio noAuthDio = Dio(BaseOptions(
      baseUrl: credentials.url,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 10),
    ));
    try {
      final Response<dynamic> response =
          await noAuthDio.get<dynamic>('/api/v1/ssh/config');
      return SshConfigResponse.fromJson(response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  // ── Projects API ──────────────────────────────────────────────────────────

  /// Returns all project directory entries from GET /api/v1/projects.
  Future<List<ProjectEntry>> listProjects() async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/projects');
      final List<dynamic> list = response.data as List<dynamic>;
      return list
          .map((dynamic e) =>
              ProjectEntry.fromJson(e as Map<String, dynamic>))
          .toList();
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  /// Returns runs filtered by [workspaceId] from
  /// GET /api/v1/runs?workspace_id={workspaceId}.
  Future<List<RunSummary>> listRunsByProject(String workspaceId) async {
    try {
      final Response<dynamic> response = await _dio.get<dynamic>(
        '/api/v1/runs',
        queryParameters: <String, dynamic>{'workspace_id': workspaceId},
      );
      final List<dynamic> list = response.data as List<dynamic>;
      return list
          .map((dynamic e) =>
              RunSummary.fromJson(e as Map<String, dynamic>))
          .toList();
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  // ── Prompt/Response API ───────────────────────────────────────────────────

  /// Returns the pending prompt for [runId], or null when HTTP 204 (no prompt).
  ///
  /// Throws [NotFoundException] on HTTP 404.
  Future<PendingPrompt?> getPendingPrompt(String runId) async {
    try {
      final Response<dynamic> response = await _dio.get<dynamic>(
        '/api/v1/runs/$runId/pending-prompt',
      );
      if (response.statusCode == 204 || response.data == null) return null;
      return PendingPrompt.fromJson(response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      // Dio throws on non-2xx; a 204 with no body may arrive as a success
      // with null data — handled above.  Any other error is remapped.
      throw _mapError(e);
    }
  }

  /// Submits the user's [response] for [promptId] on [runId].
  ///
  /// Throws [ConflictException] on HTTP 409 (stale prompt_id).
  /// Throws [GoneException] on HTTP 410 (run no longer active).
  Future<void> respondToPrompt(
      String runId, String promptId, String response) async {
    try {
      await _dio.post<dynamic>(
        '/api/v1/runs/$runId/respond',
        data: <String, dynamic>{
          'prompt_id': promptId,
          'response': response,
        },
      );
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  // ── Monitoring & Analytics API ────────────────────────────────────────────

  /// Returns cost analytics from GET /api/v1/cost-analytics.
  Future<CostAnalytics> getCostAnalytics() async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/cost-analytics');
      return CostAnalytics.fromJson(response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  /// Returns SLO compliance report from GET /api/v1/slo.
  Future<SloReport> getSloReport() async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/slo');
      return SloReport.fromJson(response.data as Map<String, dynamic>);
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  /// Returns alerts list from GET /api/v1/alerts.
  Future<List<Alert>> getAlerts() async {
    try {
      final Response<dynamic> response =
          await _dio.get<dynamic>('/api/v1/alerts');
      final Map<String, dynamic> data = response.data as Map<String, dynamic>;
      final List<dynamic> list = data['alerts'] as List<dynamic>? ?? [];
      return list
          .map((dynamic e) => Alert.fromJson(e as Map<String, dynamic>))
          .toList();
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }

  /// Searches artifacts globally via GET /api/v1/artifacts/search.
  ///
  /// [query] is required. [type] and [agent] are optional filters.
  Future<List<ArtifactSearchResult>> searchArtifacts(
    String query, {
    String? type,
    String? agent,
  }) async {
    try {
      final Map<String, dynamic> params = <String, dynamic>{'q': query};
      if (type != null && type.isNotEmpty) params['type'] = type;
      if (agent != null && agent.isNotEmpty) params['agent'] = agent;

      final Response<dynamic> response = await _dio.get<dynamic>(
        '/api/v1/artifacts/search',
        queryParameters: params,
      );
      final Map<String, dynamic> data = response.data as Map<String, dynamic>;
      final List<dynamic> list = data['results'] as List<dynamic>? ?? [];
      return list
          .map((dynamic e) =>
              ArtifactSearchResult.fromJson(e as Map<String, dynamic>))
          .toList();
    } on DioException catch (e) {
      throw _mapError(e);
    }
  }
}

// ── Riverpod Provider ─────────────────────────────────────────────────────────

/// A shared [ApiService] instance scoped to the current authentication
/// credentials.  Returns null when the user is not authenticated.
///
/// Prefer ref.read/watch(apiServiceProvider) over constructing ApiService
/// inline — inline construction creates a new Dio instance (and its internal
/// socket pool) on every call, causing unnecessary socket churn.
final apiServiceProvider = Provider<ApiService?>((ref) {
  final authState = ref.watch(authNotifierProvider);
  final creds = authState.valueOrNull;
  if (creds == null) return null;
  return ApiService(credentials: creds);
});
