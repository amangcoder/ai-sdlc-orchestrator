/// A single directory entry returned by GET /api/v1/directories.
///
/// No raw filesystem path is included — the server only exposes an opaque [id].
class DirectoryEntry {
  final String id;
  final String name;
  final String? techStack;
  final String? lastUsed;

  const DirectoryEntry({
    required this.id,
    required this.name,
    this.techStack,
    this.lastUsed,
  });

  factory DirectoryEntry.fromJson(Map<String, dynamic> json) => DirectoryEntry(
        id: json['id'] as String,
        name: json['name'] as String,
        techStack: json['tech_stack'] as String?,
        lastUsed: json['last_used'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        if (techStack != null) 'tech_stack': techStack,
        if (lastUsed != null) 'last_used': lastUsed,
      };

  @override
  bool operator ==(Object other) => other is DirectoryEntry && other.id == id;

  @override
  int get hashCode => id.hashCode;
}
