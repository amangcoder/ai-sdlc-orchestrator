import 'directory_entry.dart';

/// Response from GET /api/v1/directories/{dir_id}/children.
///
/// Contains the list of immediate sub-directory entries, the opaque parent
/// ID, the current depth, and a flag indicating whether further navigation
/// is blocked by the server-side max_browse_depth limit.
class DirectoryChildrenResponse {
  final List<DirectoryEntry> entries;
  final String parentId;
  final int depth;
  final bool atDepthLimit;

  const DirectoryChildrenResponse({
    required this.entries,
    required this.parentId,
    required this.depth,
    required this.atDepthLimit,
  });

  factory DirectoryChildrenResponse.fromJson(Map<String, dynamic> json) {
    final rawEntries = json['entries'] as List<dynamic>? ?? [];
    return DirectoryChildrenResponse(
      entries: rawEntries
          .map((e) => DirectoryEntry.fromJson(e as Map<String, dynamic>))
          .toList(),
      parentId: json['parent_id'] as String,
      depth: json['depth'] as int,
      atDepthLimit: json['at_depth_limit'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() => {
        'entries': entries.map((e) => e.toJson()).toList(),
        'parent_id': parentId,
        'depth': depth,
        'at_depth_limit': atDepthLimit,
      };
}
