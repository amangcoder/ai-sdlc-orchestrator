import 'package:flutter/material.dart';

/// Collapsible JSON tree viewer without external packages.
/// Renders maps/lists as expandable rows and leaves as syntax-colored values.
class JsonTreeNode extends StatefulWidget {
  final String? label;
  final dynamic value;
  final int depth;
  final bool initiallyExpanded;

  const JsonTreeNode({
    super.key,
    this.label,
    required this.value,
    this.depth = 0,
    this.initiallyExpanded = true,
  });

  @override
  State<JsonTreeNode> createState() => _JsonTreeNodeState();
}

class _JsonTreeNodeState extends State<JsonTreeNode> {
  late bool _expanded;

  @override
  void initState() {
    super.initState();
    _expanded = widget.depth < 2 && widget.initiallyExpanded;
  }

  @override
  Widget build(BuildContext context) {
    final value = widget.value;
    final indent = widget.depth * 16.0;

    if (value is Map<String, dynamic>) {
      return _buildMapNode(context, value, indent);
    } else if (value is List<dynamic>) {
      return _buildListNode(context, value, indent);
    } else {
      return _buildLeafNode(context, value, indent);
    }
  }

  Widget _buildMapNode(
      BuildContext context, Map<String, dynamic> map, double indent) {
    final label = widget.label;
    final summary = '{ ${map.length} ${map.length == 1 ? 'key' : 'keys'} }';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: () => setState(() => _expanded = !_expanded),
          child: Padding(
            padding: EdgeInsets.only(left: indent, top: 2, bottom: 2),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  _expanded
                      ? Icons.keyboard_arrow_down
                      : Icons.keyboard_arrow_right,
                  size: 16,
                  color: Colors.grey,
                ),
                const SizedBox(width: 4),
                if (label != null) ...[
                  Text(
                    '$label: ',
                    style: const TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
                if (!_expanded)
                  Text(
                    summary,
                    style: const TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      color: Colors.grey,
                    ),
                  )
                else
                  const Text(
                    '{',
                    style: TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      color: Colors.grey,
                    ),
                  ),
              ],
            ),
          ),
        ),
        if (_expanded) ...[
          for (final entry in map.entries)
            JsonTreeNode(
              key: ValueKey('${entry.key}_${widget.depth}'),
              label: entry.key,
              value: entry.value,
              depth: widget.depth + 1,
              initiallyExpanded: widget.initiallyExpanded,
            ),
          Padding(
            padding: EdgeInsets.only(left: indent + 20, top: 2, bottom: 2),
            child: const Text(
              '}',
              style: TextStyle(
                fontSize: 13,
                fontFamily: 'monospace',
                color: Colors.grey,
              ),
            ),
          ),
        ],
      ],
    );
  }

  Widget _buildListNode(
      BuildContext context, List<dynamic> list, double indent) {
    final label = widget.label;
    final summary = '[ ${list.length} ${list.length == 1 ? 'item' : 'items'} ]';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: () => setState(() => _expanded = !_expanded),
          child: Padding(
            padding: EdgeInsets.only(left: indent, top: 2, bottom: 2),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  _expanded
                      ? Icons.keyboard_arrow_down
                      : Icons.keyboard_arrow_right,
                  size: 16,
                  color: Colors.grey,
                ),
                const SizedBox(width: 4),
                if (label != null) ...[
                  Text(
                    '$label: ',
                    style: const TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
                if (!_expanded)
                  Text(
                    summary,
                    style: const TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      color: Colors.grey,
                    ),
                  )
                else
                  const Text(
                    '[',
                    style: TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      color: Colors.grey,
                    ),
                  ),
              ],
            ),
          ),
        ),
        if (_expanded) ...[
          for (var i = 0; i < list.length; i++)
            JsonTreeNode(
              key: ValueKey('${label}_${i}_${widget.depth}'),
              label: '[$i]',
              value: list[i],
              depth: widget.depth + 1,
              initiallyExpanded: widget.initiallyExpanded,
            ),
          Padding(
            padding: EdgeInsets.only(left: indent + 20, top: 2, bottom: 2),
            child: const Text(
              ']',
              style: TextStyle(
                fontSize: 13,
                fontFamily: 'monospace',
                color: Colors.grey,
              ),
            ),
          ),
        ],
      ],
    );
  }

  Widget _buildLeafNode(BuildContext context, dynamic value, double indent) {
    final label = widget.label;
    return Padding(
      padding: EdgeInsets.only(left: indent + 20, top: 2, bottom: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (label != null) ...[
            Text(
              '$label: ',
              style: const TextStyle(
                fontSize: 13,
                fontFamily: 'monospace',
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
          Expanded(
            child: Text(
              _formatValue(value),
              style: TextStyle(
                fontSize: 13,
                fontFamily: 'monospace',
                color: _valueColor(value),
                fontStyle: value == null ? FontStyle.italic : FontStyle.normal,
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _formatValue(dynamic value) {
    if (value == null) return 'null';
    if (value is String) return '"$value"';
    return value.toString();
  }

  Color _valueColor(dynamic value) {
    if (value == null) return Colors.grey;
    if (value is String) return Colors.green.shade700;
    if (value is num) return Colors.blue.shade700;
    if (value is bool) return Colors.orange.shade700;
    return Colors.black87;
  }
}

/// Editable variant that allows inline editing of leaf values
/// (except '***REDACTED***' values which show a lock icon).
class EditableJsonTreeNode extends StatefulWidget {
  final String? label;
  final dynamic value;
  final int depth;
  final void Function(String key, dynamic newValue)? onChanged;
  final String topLevelKey;

  const EditableJsonTreeNode({
    super.key,
    this.label,
    required this.value,
    this.depth = 0,
    this.onChanged,
    required this.topLevelKey,
  });

  @override
  State<EditableJsonTreeNode> createState() => _EditableJsonTreeNodeState();
}

class _EditableJsonTreeNodeState extends State<EditableJsonTreeNode> {
  late bool _expanded;
  bool _editing = false;
  late TextEditingController _editController;

  static const _redacted = '***REDACTED***';

  @override
  void initState() {
    super.initState();
    _expanded = widget.depth < 2;
    _editController = TextEditingController(
      text: widget.value?.toString() ?? '',
    );
  }

  @override
  void dispose() {
    _editController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final value = widget.value;
    final indent = widget.depth * 16.0;

    if (value is Map<String, dynamic>) {
      return _buildMapNode(context, value, indent);
    } else if (value is List<dynamic>) {
      return _buildListNode(context, value, indent);
    } else {
      return _buildLeafNode(context, value, indent);
    }
  }

  Widget _buildMapNode(
      BuildContext context, Map<String, dynamic> map, double indent) {
    final label = widget.label;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: () => setState(() => _expanded = !_expanded),
          child: Padding(
            padding: EdgeInsets.only(left: indent, top: 2, bottom: 2),
            child: Row(
              children: [
                Icon(
                  _expanded
                      ? Icons.keyboard_arrow_down
                      : Icons.keyboard_arrow_right,
                  size: 16,
                  color: Colors.grey,
                ),
                const SizedBox(width: 4),
                if (label != null)
                  Text(
                    '$label: ',
                    style: const TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                if (!_expanded)
                  Text(
                    '{ ${map.length} keys }',
                    style: const TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      color: Colors.grey,
                    ),
                  ),
              ],
            ),
          ),
        ),
        if (_expanded)
          for (final entry in map.entries)
            EditableJsonTreeNode(
              key: ValueKey('edit_${entry.key}_${widget.depth}'),
              label: entry.key,
              value: entry.value,
              depth: widget.depth + 1,
              topLevelKey: widget.depth == 0 ? entry.key : widget.topLevelKey,
              onChanged: widget.onChanged,
            ),
      ],
    );
  }

  Widget _buildListNode(
      BuildContext context, List<dynamic> list, double indent) {
    final label = widget.label;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: () => setState(() => _expanded = !_expanded),
          child: Padding(
            padding: EdgeInsets.only(left: indent, top: 2, bottom: 2),
            child: Row(
              children: [
                Icon(
                  _expanded
                      ? Icons.keyboard_arrow_down
                      : Icons.keyboard_arrow_right,
                  size: 16,
                  color: Colors.grey,
                ),
                const SizedBox(width: 4),
                if (label != null)
                  Text(
                    '$label: ',
                    style: const TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                if (!_expanded)
                  Text(
                    '[ ${list.length} items ]',
                    style: const TextStyle(
                      fontSize: 13,
                      fontFamily: 'monospace',
                      color: Colors.grey,
                    ),
                  ),
              ],
            ),
          ),
        ),
        if (_expanded)
          for (var i = 0; i < list.length; i++)
            EditableJsonTreeNode(
              key: ValueKey('edit_${label}_${i}_${widget.depth}'),
              label: '[$i]',
              value: list[i],
              depth: widget.depth + 1,
              topLevelKey: widget.topLevelKey,
              onChanged: widget.onChanged,
            ),
      ],
    );
  }

  Widget _buildLeafNode(BuildContext context, dynamic value, double indent) {
    final label = widget.label;
    final isRedacted = value == _redacted;

    return Padding(
      padding: EdgeInsets.only(left: indent + 20, top: 2, bottom: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (label != null)
            Text(
              '$label: ',
              style: const TextStyle(
                fontSize: 13,
                fontFamily: 'monospace',
                fontWeight: FontWeight.w500,
              ),
            ),
          if (isRedacted) ...[
            const Icon(Icons.lock_outline, size: 14, color: Colors.grey),
            const SizedBox(width: 4),
            Text(
              _redacted,
              style: TextStyle(
                fontSize: 13,
                fontFamily: 'monospace',
                color: Colors.grey.shade400,
                fontStyle: FontStyle.italic,
              ),
            ),
          ] else if (_editing) ...[
            Expanded(
              child: TextField(
                controller: _editController,
                style: const TextStyle(
                  fontSize: 13,
                  fontFamily: 'monospace',
                ),
                decoration: const InputDecoration(
                  isDense: true,
                  border: OutlineInputBorder(),
                  contentPadding:
                      EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                ),
                autofocus: true,
                onSubmitted: (v) => _commitEdit(value, v),
              ),
            ),
            IconButton(
              icon: const Icon(Icons.check, size: 16),
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
              onPressed: () => _commitEdit(value, _editController.text),
            ),
            IconButton(
              icon: const Icon(Icons.close, size: 16),
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
              onPressed: () => setState(() => _editing = false),
            ),
          ] else
            GestureDetector(
              onTap: (value is String || value is num)
                  ? () {
                      _editController.text = value.toString();
                      setState(() => _editing = true);
                    }
                  : null,
              child: Text(
                _formatValue(value),
                style: TextStyle(
                  fontSize: 13,
                  fontFamily: 'monospace',
                  color: _valueColor(value),
                  decoration: (value is String || value is num)
                      ? TextDecoration.underline
                      : null,
                  decorationColor: Colors.grey.shade300,
                ),
              ),
            ),
        ],
      ),
    );
  }

  void _commitEdit(dynamic originalValue, String newText) {
    setState(() => _editing = false);
    dynamic parsed;
    if (originalValue is int) {
      parsed = int.tryParse(newText) ?? originalValue;
    } else if (originalValue is double) {
      parsed = double.tryParse(newText) ?? originalValue;
    } else if (originalValue is bool) {
      parsed = newText.toLowerCase() == 'true';
    } else {
      parsed = newText;
    }
    widget.onChanged?.call(widget.topLevelKey, parsed);
  }

  String _formatValue(dynamic value) {
    if (value == null) return 'null';
    if (value is String) return '"$value"';
    return value.toString();
  }

  Color _valueColor(dynamic value) {
    if (value == null) return Colors.grey;
    if (value is String) return Colors.green.shade700;
    if (value is num) return Colors.blue.shade700;
    if (value is bool) return Colors.orange.shade700;
    return Colors.black87;
  }
}
