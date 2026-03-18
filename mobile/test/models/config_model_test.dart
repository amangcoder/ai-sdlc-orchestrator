import 'package:flutter_test/flutter_test.dart';
import 'package:orchestrator_mobile/models/config_model.dart';

void main() {
  group('ConfigModel.fromJson', () {
    test('deserializes config and redacted keys', () {
      final json = {
        'config': {
          'project_name': 'my-project',
          'model_routing': {'mode': 'balanced'},
          'api_key': '***REDACTED***',
        },
        'redacted_keys': ['api_key'],
      };

      final config = ConfigModel.fromJson(json);
      expect(config.config['project_name'], 'my-project');
      expect(config.config['api_key'], '***REDACTED***');
      expect(config.redactedKeys, ['api_key']);
    });

    test('handles null config', () {
      final json = <String, dynamic>{
        'config': null,
        'redacted_keys': null,
      };

      final config = ConfigModel.fromJson(json);
      expect(config.config, isEmpty);
      expect(config.redactedKeys, isEmpty);
    });

    test('handles missing fields', () {
      final json = <String, dynamic>{};

      final config = ConfigModel.fromJson(json);
      expect(config.config, isEmpty);
      expect(config.redactedKeys, isEmpty);
    });

    test('handles nested config values', () {
      final json = {
        'config': {
          'monitoring': {
            'enabled': true,
            'metrics_port': 9090,
            'alerting': {
              'webhooks': [],
            },
          },
        },
        'redacted_keys': [],
      };

      final config = ConfigModel.fromJson(json);
      final monitoring = config.config['monitoring'] as Map<String, dynamic>;
      expect(monitoring['enabled'], true);
      expect(monitoring['metrics_port'], 9090);
    });
  });
}
