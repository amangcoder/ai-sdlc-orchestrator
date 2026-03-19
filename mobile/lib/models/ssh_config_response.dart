/// Response from GET /api/v1/ssh/config (no auth required).
///
/// [configured] is true when the server has ssh_port or projects_root set.
/// [hostReachable] reflects whether a TCP probe to the SSH port succeeded.
class SshConfigResponse {
  final bool configured;
  final bool hostReachable;

  const SshConfigResponse({
    required this.configured,
    required this.hostReachable,
  });

  factory SshConfigResponse.fromJson(Map<String, dynamic> json) =>
      SshConfigResponse(
        configured: json['configured'] as bool? ?? false,
        hostReachable: json['host_reachable'] as bool? ?? false,
      );

  Map<String, dynamic> toJson() => {
        'configured': configured,
        'host_reachable': hostReachable,
      };
}
