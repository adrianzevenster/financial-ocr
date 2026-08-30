import 'package:flutter/material.dart';

class ConfidenceBar extends StatelessWidget {
  final double value; // 0.0 – 1.0
  final double height;
  final double width;

  const ConfidenceBar({
    super.key,
    required this.value,
    this.height = 6,
    this.width = 80,
  });

  Color _color() {
    if (value >= 0.94) return const Color(0xFF2E7D32); // green
    if (value >= 0.75) return const Color(0xFFF57F17); // amber
    return const Color(0xFFC62828); // red
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: width,
      height: height,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(height / 2),
        child: LinearProgressIndicator(
          value: value.clamp(0.0, 1.0),
          backgroundColor: Colors.grey.shade200,
          valueColor: AlwaysStoppedAnimation<Color>(_color()),
          minHeight: height,
        ),
      ),
    );
  }
}
