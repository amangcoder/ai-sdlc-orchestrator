import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/pending_prompt.dart';
import '../services/api_service.dart';

/// Inline card widget displayed on the RunDetailScreen when the orchestrator
/// is waiting for a user response at a clarification checkpoint.
///
/// Supports two prompt types:
///   - 'single_choice': renders one ElevatedButton per option
///   - 'free_text': renders a TextField + Submit button
///
/// Calls [onDismiss] on successful response submission (HTTP 200).
/// Shows a persistent SnackBar on ConflictException (409) or GoneException
/// (410) allowing the user to retry.
class PromptCard extends ConsumerStatefulWidget {
  final PendingPrompt prompt;
  final String runId;
  final VoidCallback onDismiss;

  const PromptCard({
    super.key,
    required this.prompt,
    required this.runId,
    required this.onDismiss,
  });

  @override
  ConsumerState<PromptCard> createState() => _PromptCardState();
}

class _PromptCardState extends ConsumerState<PromptCard> {
  bool _isSubmitting = false;
  final TextEditingController _textController = TextEditingController();

  @override
  void dispose() {
    _textController.dispose();
    super.dispose();
  }

  Future<void> _submitResponse(String response) async {
    if (response.isEmpty || _isSubmitting) return;

    setState(() => _isSubmitting = true);

    try {
      final api = ref.read(apiServiceProvider);
      if (api == null) {
        _showErrorSnackBar('Not authenticated — please re-open the app.');
        return;
      }
      await api.respondToPrompt(widget.runId, widget.prompt.promptId, response);
      // Success — dismiss the card.
      widget.onDismiss();
    } on ConflictException {
      _showRetrySnackBar(
        'The prompt has changed — please review the new question.',
      );
    } on GoneException {
      _showRetrySnackBar(
        'Run is no longer active — your response was not recorded.',
      );
    } catch (e) {
      _showRetrySnackBar('Submission failed: $e — tap to retry.');
    } finally {
      if (mounted) setState(() => _isSubmitting = false);
    }
  }

  void _showRetrySnackBar(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        // Non-dismissing: duration is long so user can retry.
        duration: const Duration(minutes: 2),
        action: SnackBarAction(
          label: 'OK',
          onPressed: () =>
              ScaffoldMessenger.of(context).hideCurrentSnackBar(),
        ),
      ),
    );
  }

  void _showErrorSnackBar(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        duration: const Duration(seconds: 10),
      ),
    );
  }

  Widget _buildSingleChoiceOptions() {
    final options = widget.prompt.options ?? [];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final option in options)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: ElevatedButton(
              onPressed: _isSubmitting ? null : () => _submitResponse(option),
              child: Text(option),
            ),
          ),
      ],
    );
  }

  Widget _buildFreeTextInput() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: _textController,
          enabled: !_isSubmitting,
          decoration: const InputDecoration(
            hintText: 'Type your response…',
            border: OutlineInputBorder(),
          ),
          maxLines: 3,
          textInputAction: TextInputAction.newline,
          onChanged: (_) => setState(() {}),
        ),
        const SizedBox(height: 8),
        ElevatedButton(
          onPressed: (_isSubmitting || _textController.text.trim().isEmpty)
              ? null
              : () => _submitResponse(_textController.text.trim()),
          child: const Text('Submit'),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      color: Theme.of(context).colorScheme.secondaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header row
            Row(
              children: [
                Icon(
                  Icons.help_outline,
                  size: 20,
                  color: Theme.of(context).colorScheme.onSecondaryContainer,
                ),
                const SizedBox(width: 8),
                Text(
                  'Orchestrator needs your input',
                  style: Theme.of(context).textTheme.titleSmall?.copyWith(
                        color: Theme.of(context)
                            .colorScheme
                            .onSecondaryContainer,
                      ),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // Question text — explicit LTR to prevent bidi spoofing (REQ-018)
            Text(
              widget.prompt.question,
              style: Theme.of(context).textTheme.bodyMedium,
              textDirection: TextDirection.ltr,
            ),
            const SizedBox(height: 16),

            // Loading indicator or input UI
            if (_isSubmitting)
              const Center(
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: 8),
                  child: CircularProgressIndicator.adaptive(),
                ),
              )
            else if (widget.prompt.type == 'single_choice')
              _buildSingleChoiceOptions()
            else
              _buildFreeTextInput(),
          ],
        ),
      ),
    );
  }
}
