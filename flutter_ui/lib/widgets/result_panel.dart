import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/extraction_result.dart';
import 'confidence_bar.dart';

// Field IDs to display in order, with human-readable labels
const _fieldLabels = <String, String>{
  'invoice_number': 'Invoice Number',
  'organization_name': 'Supplier',
  'invoice_date': 'Invoice Date',
  'total_amount': 'Total Amount',
  'currency': 'Currency',
  'purchase_order_number': 'PO Number',
  'due_date': 'Due Date',
  'vat_number': 'VAT Number',
  'recipient_organization': 'Recipient',
};

class ResultPanel extends StatelessWidget {
  final ExtractionResult result;
  final String filename;
  final VoidCallback onReset;

  const ResultPanel({
    super.key,
    required this.result,
    required this.filename,
    required this.onReset,
  });

  @override
  Widget build(BuildContext context) {
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 700),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Header(result: result, filename: filename, onReset: onReset),
          const SizedBox(height: 20),
          _FieldsCard(result: result),
          if (result.proposedFilename != null) ...[
            const SizedBox(height: 16),
            _FilenameCard(result: result),
          ],
          if (result.reasonCodes.isNotEmpty) ...[
            const SizedBox(height: 16),
            _ReasonCodesCard(codes: result.reasonCodes),
          ],
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _Header extends StatelessWidget {
  final ExtractionResult result;
  final String filename;
  final VoidCallback onReset;

  const _Header({
    required this.result,
    required this.filename,
    required this.onReset,
  });

  Color _statusColor(BuildContext context) {
    if (result.isAccepted) return const Color(0xFF2E7D32);
    if (result.isReview) return const Color(0xFFF57F17);
    return const Color(0xFFC62828);
  }

  IconData _statusIcon() {
    if (result.isAccepted) return Icons.check_circle_rounded;
    if (result.isReview) return Icons.warning_amber_rounded;
    return Icons.cancel_rounded;
  }

  String _statusLabel() {
    if (result.isAccepted) return 'Auto-apply ready';
    if (result.isReview) return 'Needs review';
    return 'Quarantined';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final statusColor = _statusColor(context);

    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                filename,
                style: theme.textTheme.titleLarge
                    ?.copyWith(fontWeight: FontWeight.w600),
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 4),
              Row(
                children: [
                  Text(
                    result.documentType.toUpperCase(),
                    style: theme.textTheme.labelMedium?.copyWith(
                      color: theme.colorScheme.primary,
                      letterSpacing: 1,
                    ),
                  ),
                  if (result.ocrUsed) ...[
                    const SizedBox(width: 8),
                    Chip(
                      label: const Text('OCR'),
                      labelStyle:
                          const TextStyle(fontSize: 11, color: Colors.white),
                      backgroundColor: Colors.deepPurple.shade300,
                      padding: EdgeInsets.zero,
                      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      visualDensity: VisualDensity.compact,
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
        Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(_statusIcon(), color: statusColor, size: 20),
                const SizedBox(width: 4),
                Text(
                  _statusLabel(),
                  style: TextStyle(
                    color: statusColor,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              'Confidence: ${(result.overallConfidence * 100).toStringAsFixed(0)}%',
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
        const SizedBox(width: 12),
        IconButton(
          icon: const Icon(Icons.refresh_rounded),
          tooltip: 'Process another file',
          onPressed: onReset,
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------

class _FieldsCard extends StatelessWidget {
  final ExtractionResult result;
  const _FieldsCard({required this.result});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final fields = result.fields;

    // Show required fields first, then optional
    final ordered = _fieldLabels.keys
        .where((id) => fields.containsKey(id))
        .toList();

    return Card(
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: theme.colorScheme.outlineVariant),
      ),
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Extracted Fields', style: theme.textTheme.titleMedium),
            const SizedBox(height: 16),
            Table(
              columnWidths: const {
                0: IntrinsicColumnWidth(),
                1: FlexColumnWidth(),
                2: IntrinsicColumnWidth(),
                3: IntrinsicColumnWidth(),
              },
              defaultVerticalAlignment: TableCellVerticalAlignment.middle,
              children: [
                TableRow(
                  decoration: BoxDecoration(
                    border: Border(
                      bottom: BorderSide(color: theme.colorScheme.outlineVariant),
                    ),
                  ),
                  children: [
                    _th(context, 'Field'),
                    _th(context, 'Value'),
                    _th(context, 'Confidence'),
                    const SizedBox.shrink(),
                  ],
                ),
                ...ordered.map((id) {
                  final f = fields[id]!;
                  final label = _fieldLabels[id] ?? id;
                  final missing = f.normalized == null && f.raw == null;
                  return TableRow(
                    children: [
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 4),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            if (f.required)
                              Tooltip(
                                message: 'Required field',
                                child: Icon(
                                  Icons.circle,
                                  size: 6,
                                  color: theme.colorScheme.primary,
                                ),
                              ),
                            const SizedBox(width: 6),
                            Text(
                              label,
                              style: theme.textTheme.bodyMedium?.copyWith(
                                color: theme.colorScheme.onSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 8),
                        child: missing
                            ? Text(
                                '—',
                                style: TextStyle(color: theme.colorScheme.error),
                              )
                            : SelectableText(
                                f.displayValue,
                                style: theme.textTheme.bodyMedium?.copyWith(
                                  fontWeight: FontWeight.w500,
                                ),
                              ),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 4),
                        child: missing
                            ? const SizedBox.shrink()
                            : Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  ConfidenceBar(value: f.confidence),
                                  const SizedBox(width: 6),
                                  Text(
                                    '${(f.confidence * 100).toStringAsFixed(0)}%',
                                    style: theme.textTheme.bodySmall,
                                  ),
                                ],
                              ),
                      ),
                      const SizedBox.shrink(),
                    ],
                  );
                }),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _th(BuildContext context, String text) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8, right: 8),
      child: Text(
        text,
        style: Theme.of(context).textTheme.labelSmall?.copyWith(
          color: Theme.of(context).colorScheme.outline,
          letterSpacing: 0.8,
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _FilenameCard extends StatefulWidget {
  final ExtractionResult result;
  const _FilenameCard({required this.result});

  @override
  State<_FilenameCard> createState() => _FilenameCardState();
}

class _FilenameCardState extends State<_FilenameCard> {
  bool _copied = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final filename = widget.result.proposedFilename!;

    return Card(
      elevation: 0,
      color: theme.colorScheme.primaryContainer.withAlpha(80),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: theme.colorScheme.primaryContainer),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
        child: Row(
          children: [
            Icon(
              Icons.drive_file_rename_outline_rounded,
              color: theme.colorScheme.primary,
              size: 20,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Proposed filename',
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.primary,
                    ),
                  ),
                  const SizedBox(height: 2),
                  SelectableText(
                    filename,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      fontFamily: 'monospace',
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  if (widget.result.proposedCategory != null) ...[
                    const SizedBox(height: 2),
                    Text(
                      widget.result.proposedCategory!,
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.outline,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            IconButton(
              icon: Icon(
                _copied ? Icons.check_rounded : Icons.copy_rounded,
                size: 18,
                color: _copied
                    ? const Color(0xFF2E7D32)
                    : theme.colorScheme.outline,
              ),
              tooltip: 'Copy filename',
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: filename));
                if (!mounted) return;
                setState(() => _copied = true);
                await Future.delayed(const Duration(seconds: 2));
                if (mounted) setState(() => _copied = false);
              },
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _ReasonCodesCard extends StatelessWidget {
  final List<String> codes;
  const _ReasonCodesCard({required this.codes});

  static const _labels = <String, String>{
    'missing_required_field': 'Missing required field',
    'low_confidence': 'Low confidence value',
    'invalid_date': 'Invalid date format',
    'invalid_amount': 'Invalid amount',
    'invalid_currency': 'Invalid currency',
    'ambiguous_currency': 'Ambiguous currency',
    'cross_field_inconsistency': 'Cross-field inconsistency',
    'ocr_quality_too_low': 'OCR quality too low',
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      elevation: 0,
      color: theme.colorScheme.errorContainer.withAlpha(80),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: theme.colorScheme.errorContainer),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.info_outline_rounded,
                    size: 16, color: theme.colorScheme.error),
                const SizedBox(width: 8),
                Text(
                  'Quarantine reasons',
                  style: theme.textTheme.labelMedium
                      ?.copyWith(color: theme.colorScheme.error),
                ),
              ],
            ),
            const SizedBox(height: 8),
            ...codes.map(
              (c) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Text(
                  '• ${_labels[c] ?? c.replaceAll('_', ' ')}',
                  style: theme.textTheme.bodySmall,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
