import 'package:flutter/material.dart';

class DropZone extends StatefulWidget {
  final VoidCallback onTap;
  final bool isLoading;

  const DropZone({super.key, required this.onTap, this.isLoading = false});

  @override
  State<DropZone> createState() => _DropZoneState();
}

class _DropZoneState extends State<DropZone> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = _hover
        ? theme.colorScheme.primary
        : theme.colorScheme.outline;

    return MouseRegion(
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: widget.isLoading ? null : widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          constraints: const BoxConstraints(maxWidth: 520),
          padding: const EdgeInsets.symmetric(vertical: 48, horizontal: 32),
          decoration: BoxDecoration(
            color: _hover
                ? theme.colorScheme.primary.withAlpha(12)
                : theme.colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
              color: color,
              width: 2,
              style: BorderStyle.solid,
            ),
          ),
          child: widget.isLoading
              ? const Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    CircularProgressIndicator(),
                    SizedBox(height: 16),
                    Text('Extracting…', style: TextStyle(fontSize: 16)),
                  ],
                )
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.upload_file_rounded,
                      size: 56,
                      color: color,
                    ),
                    const SizedBox(height: 16),
                    Text(
                      'Drop a file or click to browse',
                      style: theme.textTheme.titleMedium?.copyWith(
                        color: theme.colorScheme.onSurface,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      'PDF, PNG, JPEG, TIFF, BMP, WEBP',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.outline,
                      ),
                    ),
                  ],
                ),
        ),
      ),
    );
  }
}
