import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models/extraction_result.dart';

class ApiException implements Exception {
  final String message;
  final int? statusCode;
  const ApiException(this.message, {this.statusCode});
  @override
  String toString() => message;
}

class ApiService {
  final String baseUrl;

  const ApiService({this.baseUrl = 'http://localhost:8000'});

  Future<ExtractionResult> extract({
    required Uint8List bytes,
    required String filename,
  }) async {
    final uri = Uri.parse('$baseUrl/extract');
    final request = http.MultipartRequest('POST', uri)
      ..files.add(
        http.MultipartFile.fromBytes(
          'file',
          bytes,
          filename: filename,
        ),
      );

    http.StreamedResponse streamed;
    try {
      streamed = await request.send().timeout(const Duration(seconds: 120));
    } catch (e) {
      throw ApiException(
        'Cannot reach the finextract server at $baseUrl.\n'
        'Start it with: uvicorn finextract.server:app --reload',
      );
    }

    final body = await streamed.stream.bytesToString();

    if (streamed.statusCode == 200) {
      return ExtractionResult.fromJson(
        jsonDecode(body) as Map<String, dynamic>,
      );
    }

    // Try to parse error detail from FastAPI
    String detail = 'Unknown error';
    try {
      final err = jsonDecode(body) as Map<String, dynamic>;
      detail = err['detail'] as String? ?? detail;
    } catch (_) {}

    throw ApiException(detail, statusCode: streamed.statusCode);
  }

  Future<bool> checkHealth() async {
    try {
      final resp = await http
          .get(Uri.parse('$baseUrl/health'))
          .timeout(const Duration(seconds: 3));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }
}
