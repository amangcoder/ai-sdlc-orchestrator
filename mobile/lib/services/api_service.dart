import 'dart:io';
import 'package:dio/dio.dart';
import '../models/run_summary.dart';
import '../models/run_detail.dart';
import '../models/config_model.dart';
import '../models/directory_entry.dart';
import '../services/secure_storage_service.dart';

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

class ServerException implements Exception {
  final String message;
  const ServerException([this.message = 'Server error']);
}

class NetworkException implements Exception {
  final String message;
  const NetworkException([this.message = 'Network error']);
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
          } else if (statusCode == 429) {
            handler.reject(DioException(
              requestOptions: error.requestOptions,
              error: const RateLimitException(),
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
}
