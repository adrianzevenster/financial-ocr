import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../models/extraction_result.dart';
import '../services/api_service.dart';
import '../widgets/drop_zone.dart';
import '../widgets/result_panel.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _api = const ApiService();

  bool _loading = false;
  ExtractionResult? _result;
  String _filename = '';
  String? _error;
  bool _serverOnline = false;

  @override
  void initState() {
    super.initState();
    _checkServer();
  }

  Future<void> _checkServer() async {
    final ok = await _api.checkHealth();
    if (mounted) setState(() => _serverOnline = ok);
  }

  Future<void> _pickFile() async {
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'tif', 'bmp', 'webp'],
      withData: true,
    );
    if (picked == null || picked.files.isEmpty) return;
    final file = picked.files.first;
    if (file.bytes == null) return;
    await _process(file.bytes!, file.name);
  }

  Future<void> _process(Uint8List bytes, String filename) async {
    setState(() {
      _loading = true;
      _error = null;
      _result = null;
      _filename = filename;
    });

    try {
      final result = await _api.extract(
        bytes: bytes,
        filename: filename,
      );
      if (mounted) {
        setState(() {
          _result = result;
          _loading = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  void _reset() {
    setState(() {
      _result = null;
      _error = null;
      _filename = '';
    });
    _checkServer();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Row(
          children: [
            Icon(Icons.receipt_long_rounded, size: 22),
            SizedBox(width: 8),
            Text('finextract'),
          ],
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: _ServerStatus(online: _serverOnline, onRefresh: _checkServer),
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                if (_result != null)
                  ResultPanel(
                    result: _result!,
                    filename: _filename,
                    onReset: _reset,
                  )
                else ...[
                  DropZone(
                    onTap: _pickFile,
                    isLoading: _loading,
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 20),
                    _ErrorBanner(message: _error!),
                  ],
                  if (!_serverOnline) ...[
                    const SizedBox(height: 20),
                    _ServerWarning(onRetry: _checkServer),
                  ],
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _ServerStatus extends StatelessWidget {
  final bool online;
  final VoidCallback onRefresh;

  const _ServerStatus({required this.online, required this.onRefresh});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onRefresh,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.circle,
            size: 10,
            color: online ? const Color(0xFF2E7D32) : Colors.grey,
          ),
          const SizedBox(width: 6),
          Text(
            online ? 'Server online' : 'Server offline',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  final String message;
  const _ErrorBanner({required this.message});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      constraints: const BoxConstraints(maxWidth: 520),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.colorScheme.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.error_outline_rounded,
              color: theme.colorScheme.error, size: 20),
          const SizedBox(width: 12),
          Expanded(
            child: Text(message, style: theme.textTheme.bodyMedium),
          ),
        ],
      ),
    );
  }
}

class _ServerWarning extends StatelessWidget {
  final VoidCallback onRetry;
  const _ServerWarning({required this.onRetry});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      constraints: const BoxConstraints(maxWidth: 520),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.colorScheme.tertiaryContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.warning_amber_rounded,
                  size: 18, color: theme.colorScheme.tertiary),
              const SizedBox(width: 8),
              Text('API server not running',
                  style: theme.textTheme.titleSmall),
            ],
          ),
          const SizedBox(height: 8),
          const SelectableText(
            'uvicorn finextract.server:app --reload',
            style: TextStyle(fontFamily: 'monospace', fontSize: 13),
          ),
          const SizedBox(height: 8),
          TextButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh_rounded, size: 16),
            label: const Text('Retry'),
          ),
        ],
      ),
    );
  }
}
