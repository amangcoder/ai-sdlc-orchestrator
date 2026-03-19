/// Represents a project directory entry returned by GET /api/v1/projects.
///
/// NOTE: The API deliberately omits a 'path' field to avoid disclosing
/// filesystem paths to mobile clients.
class ProjectEntry {
  final String id;
  final String name;
  final DateTime lastModified;
  final int runCount;

  const ProjectEntry({
    required this.id,
    required this.name,
    required this.lastModified,
    required this.runCount,
  });

  factory ProjectEntry.fromJson(Map<String, dynamic> json) {
    return ProjectEntry(
      id: json['id'] as String,
      name: json['name'] as String,
      lastModified: DateTime.parse(json['last_modified'] as String),
      runCount: json['run_count'] as int? ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'last_modified': lastModified.toIso8601String(),
        'run_count': runCount,
      };
}
