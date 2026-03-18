class ConfigModel {
  final Map<String, dynamic> config;
  final List<String> redactedKeys;

  const ConfigModel({
    required this.config,
    required this.redactedKeys,
  });

  factory ConfigModel.fromJson(Map<String, dynamic> json) {
    return ConfigModel(
      config: json['config'] as Map<String, dynamic>? ?? {},
      redactedKeys: (json['redacted_keys'] as List<dynamic>?)
              ?.cast<String>() ??
          [],
    );
  }
}
