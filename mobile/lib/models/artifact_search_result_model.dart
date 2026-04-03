/// A single artifact search result from GET /api/v1/artifacts/search.
class ArtifactSearchResult {
  final String artifactName;
  final String runId;
  final String schema;
  final String agent;
  final int version;
  final DateTime updatedAt;

  const ArtifactSearchResult({
    required this.artifactName,
    required this.runId,
    required this.schema,
    required this.agent,
    required this.version,
    required this.updatedAt,
  });

  factory ArtifactSearchResult.fromJson(Map<String, dynamic> json) {
    return ArtifactSearchResult(
      artifactName: json['artifact_name'] as String? ?? '',
      runId: json['run_id'] as String? ?? '',
      schema: json['schema'] as String? ?? '',
      agent: json['agent'] as String? ?? '',
      version: json['version'] as int? ?? 1,
      updatedAt: json['updated_at'] != null
          ? DateTime.tryParse(json['updated_at'] as String) ?? DateTime.now()
          : DateTime.now(),
    );
  }

  Map<String, dynamic> toJson() => {
        'artifact_name': artifactName,
        'run_id': runId,
        'schema': schema,
        'agent': agent,
        'version': version,
        'updated_at': updatedAt.toIso8601String(),
      };
}
