import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/auth_provider.dart';
import '../services/secure_storage_service.dart';
import '../services/api_service.dart';

/// First-launch screen and settings screen for entering server credentials.
/// Shown automatically when no credentials are saved (GoRouter redirect).
class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  final _formKey = GlobalKey<FormState>();
  final _urlController = TextEditingController();
  final _apiKeyController = TextEditingController();
  bool _obscureApiKey = true;
  bool _isTesting = false;
  bool _isSaving = false;
  bool _hasExistingCredentials = false;

  @override
  void initState() {
    super.initState();
    _loadExistingCredentials();
  }

  Future<void> _loadExistingCredentials() async {
    final creds = await ref.read(authNotifierProvider.future);
    if (creds != null && mounted) {
      setState(() {
        _urlController.text = creds.url;
        _apiKeyController.text = creds.apiKey;
        _hasExistingCredentials = true;
      });
    }
  }

  @override
  void dispose() {
    _urlController.dispose();
    _apiKeyController.dispose();
    super.dispose();
  }

  Future<void> _testConnection() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _isTesting = true);
    try {
      final tempCreds = Credentials(
        url: _urlController.text.trim(),
        apiKey: _apiKeyController.text.trim(),
      );
      final api = ApiService(credentials: tempCreds);
      final health = await api.getHealth();
      final version = health['version'] as String? ?? 'unknown';
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Connected — server v$version'),
            backgroundColor: Colors.green,
          ),
        );
      }
    } on NetworkException {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Cannot reach server — check Tailscale is connected'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Connection failed: $e'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _isTesting = false);
    }
  }

  Future<void> _saveCredentials() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _isSaving = true);
    try {
      final url = _urlController.text.trim();
      final apiKey = _apiKeyController.text.trim();
      await ref.read(authNotifierProvider.notifier).save(url, apiKey);
      if (mounted) {
        context.go('/');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to save: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: _hasExistingCredentials,
      onPopInvoked: (didPop) {
        if (!didPop) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('Please configure the server to continue'),
            ),
          );
        }
      },
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Server Settings'),
          automaticallyImplyLeading: _hasExistingCredentials,
        ),
        body: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Icon(
                  Icons.cloud_sync,
                  size: 64,
                  color: Colors.blue,
                ),
                const SizedBox(height: 24),
                Text(
                  'Connect to Orchestrator',
                  style: Theme.of(context).textTheme.headlineSmall,
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 8),
                Text(
                  'Enter the server URL and API key to connect to your orchestrator instance.',
                  style: Theme.of(context).textTheme.bodyMedium,
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 32),

                // Server URL field
                TextFormField(
                  controller: _urlController,
                  decoration: const InputDecoration(
                    labelText: 'Server URL',
                    hintText: 'http://100.x.x.x:8090',
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.link),
                  ),
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  validator: (value) {
                    if (value == null || value.trim().isEmpty) {
                      return 'Server URL is required';
                    }
                    final trimmed = value.trim();
                    if (!trimmed.startsWith('http://') &&
                        !trimmed.startsWith('https://')) {
                      return 'URL must start with http:// or https://';
                    }
                    return null;
                  },
                ),
                const SizedBox(height: 16),

                // API Key field
                TextFormField(
                  controller: _apiKeyController,
                  obscureText: _obscureApiKey,
                  decoration: InputDecoration(
                    labelText: 'API Key',
                    border: const OutlineInputBorder(),
                    prefixIcon: const Icon(Icons.key),
                    suffixIcon: IconButton(
                      icon: Icon(
                        _obscureApiKey
                            ? Icons.visibility
                            : Icons.visibility_off,
                      ),
                      onPressed: () =>
                          setState(() => _obscureApiKey = !_obscureApiKey),
                      tooltip:
                          _obscureApiKey ? 'Show API key' : 'Hide API key',
                    ),
                  ),
                  autocorrect: false,
                  validator: (value) {
                    if (value == null || value.trim().isEmpty) {
                      return 'API key is required';
                    }
                    return null;
                  },
                ),
                const SizedBox(height: 24),

                // Test Connection button
                OutlinedButton.icon(
                  onPressed: _isTesting ? null : _testConnection,
                  icon: _isTesting
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.wifi_find),
                  label:
                      Text(_isTesting ? 'Testing...' : 'Test Connection'),
                ),
                const SizedBox(height: 12),

                // Save button
                ElevatedButton.icon(
                  onPressed: _isSaving ? null : _saveCredentials,
                  icon: _isSaving
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save),
                  label: Text(_isSaving ? 'Saving...' : 'Save'),
                  style: ElevatedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 16),
                  ),
                ),
                const SizedBox(height: 32),

                // QR code hint
                Builder(
                  builder: (context) {
                    final url = _urlController.text.trim();
                    final qrUrl =
                        url.isNotEmpty ? '$url/api/v1/setup/qr' : null;
                    return Column(
                      children: [
                        const Divider(),
                        const SizedBox(height: 12),
                        Text(
                          'Or scan the QR code at',
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                        if (qrUrl != null)
                          TextButton(
                            onPressed: () {
                              // Open in system browser
                              // Using url_launcher would be ideal but we keep deps minimal
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(
                                  content: SelectableText(qrUrl),
                                  action: SnackBarAction(
                                    label: 'Copy',
                                    onPressed: () {},
                                  ),
                                ),
                              );
                            },
                            child: Text(qrUrl),
                          )
                        else
                          Text(
                            '{server_url}/api/v1/setup/qr',
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                      ],
                    );
                  },
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
