import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../providers/auth_provider.dart';
import '../services/secure_storage_service.dart';
import '../services/api_service.dart';
import '../models/ssh_credentials.dart';

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

  // ── SSH section state ───────────────────────────────────────────────────
  final _sshHostController = TextEditingController();
  final _sshPortController = TextEditingController(text: '22');
  final _sshUsernameController = TextEditingController();
  // Password and private key are never pre-populated — only masked indicators
  bool _hasSavedPassword = false;
  bool _hasSavedPrivateKey = false;
  bool _isSavingSsh = false;
  bool _isClearingSsh = false;
  bool? _sshHostReachable; // null = unknown, true/false = probe result

  final _storageService = SecureStorageService();

  @override
  void initState() {
    super.initState();
    _loadExistingCredentials();
    _loadSshCredentials();
  }

  Future<void> _loadExistingCredentials() async {
    final creds = await ref.read(authNotifierProvider.future);
    if (creds != null && mounted) {
      setState(() {
        _urlController.text = creds.url;
        _apiKeyController.text = creds.apiKey;
        _hasExistingCredentials = true;
      });
      _probeSshReachability(creds);
    }
  }

  Future<void> _loadSshCredentials() async {
    final ssh = await _storageService.loadSshCredentials();
    if (!mounted) return;
    if (ssh != null) {
      setState(() {
        _sshHostController.text = ssh.host;
        _sshPortController.text = ssh.port.toString();
        _sshUsernameController.text = ssh.username;
        _hasSavedPassword = ssh.password != null && ssh.password!.isNotEmpty;
        _hasSavedPrivateKey =
            ssh.privateKeyPem != null && ssh.privateKeyPem!.isNotEmpty;
      });
    }
  }

  Future<void> _probeSshReachability(Credentials creds) async {
    try {
      final api = ApiService(credentials: creds);
      final config = await api.getSshConfig();
      if (mounted) {
        setState(() => _sshHostReachable = config.hostReachable);
      }
    } catch (_) {
      if (mounted) setState(() => _sshHostReachable = false);
    }
  }

  @override
  void dispose() {
    _urlController.dispose();
    _apiKeyController.dispose();
    _sshHostController.dispose();
    _sshPortController.dispose();
    _sshUsernameController.dispose();
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

  Future<void> _saveSshCredentials() async {
    setState(() => _isSavingSsh = true);
    try {
      final host = _sshHostController.text.trim();
      final port =
          int.tryParse(_sshPortController.text.trim()) ?? 22;
      final username = _sshUsernameController.text.trim();

      if (host.isEmpty || username.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text('Host and username are required for SSH')),
        );
        return;
      }

      // Load existing creds to preserve saved password/key unless being cleared
      final existing = await _storageService.loadSshCredentials();

      await _storageService.saveSshCredentials(SshCredentials(
        host: host,
        port: port,
        username: username,
        // Preserve existing password/key (never re-read from UI)
        password: existing?.password,
        privateKeyPem: existing?.privateKeyPem,
      ));

      if (mounted) {
        setState(() {
          _hasSavedPassword = existing?.password != null &&
              existing!.password!.isNotEmpty;
          _hasSavedPrivateKey = existing?.privateKeyPem != null &&
              existing!.privateKeyPem!.isNotEmpty;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('SSH credentials saved'),
            backgroundColor: Colors.green,
          ),
        );
        // Re-probe reachability
        final creds = await ref.read(authNotifierProvider.future);
        if (creds != null) _probeSshReachability(creds);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to save SSH credentials: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isSavingSsh = false);
    }
  }

  Future<void> _clearSshCredentials() async {
    setState(() => _isClearingSsh = true);
    try {
      await _storageService.clearSshCredentials();
      if (mounted) {
        _sshHostController.clear();
        _sshPortController.text = '22';
        _sshUsernameController.clear();
        setState(() {
          _hasSavedPassword = false;
          _hasSavedPrivateKey = false;
          _sshHostReachable = null;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('SSH credentials cleared')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to clear SSH credentials: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isClearingSsh = false);
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
                    labelText: 'API Key (optional)',
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
                  validator: (_) => null,
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

                // ── SSH Terminal section ───────────────────────────────
                _buildSshSection(),

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

  Widget _buildSshSection() {
    final theme = Theme.of(context);
    return Theme(
      data: theme.copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        title: Row(
          children: [
            const Icon(Icons.terminal, size: 20),
            const SizedBox(width: 8),
            const Text('SSH Terminal'),
            const SizedBox(width: 8),
            // Reachability status chip
            if (_sshHostReachable != null)
              Chip(
                label: Text(
                  _sshHostReachable! ? 'Reachable' : 'Unreachable',
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: _sshHostReachable!
                        ? Colors.green.shade800
                        : Colors.red.shade800,
                  ),
                ),
                backgroundColor: _sshHostReachable!
                    ? Colors.green.shade100
                    : Colors.red.shade100,
                padding: EdgeInsets.zero,
                materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                visualDensity: VisualDensity.compact,
              ),
          ],
        ),
        subtitle: const Text(
            'Configure direct SSH access to the orchestrator host'),
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Host
                TextFormField(
                  controller: _sshHostController,
                  decoration: const InputDecoration(
                    labelText: 'Host',
                    hintText: '100.x.x.x',
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.dns),
                  ),
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                ),
                const SizedBox(height: 12),

                // Port
                TextFormField(
                  controller: _sshPortController,
                  decoration: const InputDecoration(
                    labelText: 'Port',
                    hintText: '22',
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.numbers),
                  ),
                  keyboardType: TextInputType.number,
                ),
                const SizedBox(height: 12),

                // Username
                TextFormField(
                  controller: _sshUsernameController,
                  decoration: const InputDecoration(
                    labelText: 'Username',
                    hintText: 'root',
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.person),
                  ),
                  autocorrect: false,
                ),
                const SizedBox(height: 12),

                // Password — masked, shows placeholder if saved
                TextFormField(
                  readOnly: true,
                  obscureText: true,
                  initialValue:
                      _hasSavedPassword ? '••••••••' : null,
                  decoration: InputDecoration(
                    labelText: 'Password',
                    hintText: _hasSavedPassword
                        ? '(password saved)'
                        : 'Leave blank to use private key',
                    border: const OutlineInputBorder(),
                    prefixIcon: const Icon(Icons.lock),
                    suffixIcon: _hasSavedPassword
                        ? IconButton(
                            icon: const Icon(Icons.clear),
                            tooltip: 'Clear saved password',
                            onPressed: () async {
                              final existing = await _storageService
                                  .loadSshCredentials();
                              if (existing != null) {
                                await _storageService.saveSshCredentials(
                                    SshCredentials(
                                  host: existing.host,
                                  port: existing.port,
                                  username: existing.username,
                                  // Clear password only
                                  privateKeyPem: existing.privateKeyPem,
                                ));
                              }
                              if (mounted) {
                                setState(() => _hasSavedPassword = false);
                              }
                            },
                          )
                        : IconButton(
                            icon: const Icon(Icons.edit),
                            tooltip: 'Set password',
                            onPressed: () =>
                                _showPasswordDialog(),
                          ),
                  ),
                ),
                const SizedBox(height: 12),

                // Private key — masked, shows placeholder if saved
                TextFormField(
                  readOnly: true,
                  maxLines: 1,
                  initialValue:
                      _hasSavedPrivateKey ? '(private key saved)' : null,
                  decoration: InputDecoration(
                    labelText: 'Private Key PEM',
                    hintText: _hasSavedPrivateKey
                        ? '(private key saved)'
                        : 'Paste PEM key or leave blank',
                    border: const OutlineInputBorder(),
                    prefixIcon: const Icon(Icons.vpn_key),
                    suffixIcon: _hasSavedPrivateKey
                        ? IconButton(
                            icon: const Icon(Icons.clear),
                            tooltip: 'Clear saved private key',
                            onPressed: () async {
                              final existing = await _storageService
                                  .loadSshCredentials();
                              if (existing != null) {
                                await _storageService.saveSshCredentials(
                                    SshCredentials(
                                  host: existing.host,
                                  port: existing.port,
                                  username: existing.username,
                                  password: existing.password,
                                  // Clear key only
                                ));
                              }
                              if (mounted) {
                                setState(() => _hasSavedPrivateKey = false);
                              }
                            },
                          )
                        : IconButton(
                            icon: const Icon(Icons.edit),
                            tooltip: 'Set private key',
                            onPressed: () => _showPrivateKeyDialog(),
                          ),
                  ),
                ),
                const SizedBox(height: 16),

                // Save / Clear buttons
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed:
                            _isClearingSsh ? null : _clearSshCredentials,
                        icon: _isClearingSsh
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                    strokeWidth: 2),
                              )
                            : const Icon(Icons.delete_outline),
                        label: const Text('Clear Credentials'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton.icon(
                        onPressed:
                            _isSavingSsh ? null : _saveSshCredentials,
                        icon: _isSavingSsh
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: Colors.white),
                              )
                            : const Icon(Icons.save),
                        label: const Text('Save'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Shows a dialog to enter a new SSH password without displaying it.
  Future<void> _showPasswordDialog() async {
    final controller = TextEditingController();
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Set SSH Password'),
        content: TextField(
          controller: controller,
          obscureText: true,
          autofocus: true,
          decoration: const InputDecoration(
            labelText: 'Password',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () async {
              final pwd = controller.text;
              if (pwd.isEmpty) {
                Navigator.pop(ctx);
                return;
              }
              final existing =
                  await _storageService.loadSshCredentials();
              await _storageService.saveSshCredentials(SshCredentials(
                host: existing?.host ?? _sshHostController.text.trim(),
                port: int.tryParse(_sshPortController.text) ?? 22,
                username: existing?.username ??
                    _sshUsernameController.text.trim(),
                password: pwd,
                privateKeyPem: existing?.privateKeyPem,
              ));
              if (mounted) {
                setState(() => _hasSavedPassword = true);
              }
              if (ctx.mounted) Navigator.pop(ctx);
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
    controller.dispose();
  }

  /// Shows a dialog to paste an SSH private key PEM without displaying it.
  Future<void> _showPrivateKeyDialog() async {
    final controller = TextEditingController();
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Set Private Key PEM'),
        content: TextField(
          controller: controller,
          maxLines: 6,
          autofocus: true,
          obscureText: false,
          decoration: const InputDecoration(
            labelText: 'PEM Key',
            hintText: '-----BEGIN RSA PRIVATE KEY-----\n...',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () async {
              final pem = controller.text.trim();
              if (pem.isEmpty) {
                Navigator.pop(ctx);
                return;
              }
              final existing =
                  await _storageService.loadSshCredentials();
              await _storageService.saveSshCredentials(SshCredentials(
                host: existing?.host ?? _sshHostController.text.trim(),
                port: int.tryParse(_sshPortController.text) ?? 22,
                username: existing?.username ??
                    _sshUsernameController.text.trim(),
                password: existing?.password,
                privateKeyPem: pem,
              ));
              if (mounted) {
                setState(() => _hasSavedPrivateKey = true);
              }
              if (ctx.mounted) Navigator.pop(ctx);
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
    controller.dispose();
  }
}
