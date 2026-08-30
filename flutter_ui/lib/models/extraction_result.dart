class FieldResult {
  final String? raw;
  final String? normalized;
  final double confidence;
  final bool required;

  const FieldResult({
    this.raw,
    this.normalized,
    required this.confidence,
    required this.required,
  });

  factory FieldResult.fromJson(Map<String, dynamic> json) => FieldResult(
        raw: json['raw'] as String?,
        normalized: json['normalized'] as String?,
        confidence: (json['confidence'] as num).toDouble(),
        required: json['required'] as bool,
      );

  String get displayValue => normalized ?? raw ?? '—';
}

class ExtractionResult {
  final String documentType;
  final double classificationConfidence;
  final Map<String, FieldResult> fields;
  final String? proposedFilename;
  final String? proposedCategory;
  final String validationStatus;
  final double overallConfidence;
  final bool ocrUsed;
  final int pageCount;
  final List<String> reasonCodes;

  const ExtractionResult({
    required this.documentType,
    required this.classificationConfidence,
    required this.fields,
    this.proposedFilename,
    this.proposedCategory,
    required this.validationStatus,
    required this.overallConfidence,
    required this.ocrUsed,
    required this.pageCount,
    required this.reasonCodes,
  });

  factory ExtractionResult.fromJson(Map<String, dynamic> json) {
    final rawFields = json['fields'] as Map<String, dynamic>;
    return ExtractionResult(
      documentType: json['document_type'] as String,
      classificationConfidence:
          (json['classification_confidence'] as num).toDouble(),
      fields: rawFields.map(
        (k, v) => MapEntry(k, FieldResult.fromJson(v as Map<String, dynamic>)),
      ),
      proposedFilename: json['proposed_filename'] as String?,
      proposedCategory: json['proposed_category'] as String?,
      validationStatus: json['validation_status'] as String,
      overallConfidence: (json['overall_confidence'] as num).toDouble(),
      ocrUsed: json['ocr_used'] as bool,
      pageCount: json['page_count'] as int,
      reasonCodes: List<String>.from(json['reason_codes'] as List),
    );
  }

  bool get isAccepted => validationStatus == 'accepted';
  bool get isReview => validationStatus == 'review';
  bool get isRejected => validationStatus == 'rejected';
}
