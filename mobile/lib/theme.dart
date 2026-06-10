import 'package:flutter/material.dart';

class C {
  static const bg = Color(0xFF0B0E14);
  static const card = Color(0xFF141925);
  static const card2 = Color(0xFF1B2333);
  static const line = Color(0xFF232C3D);
  static const text = Color(0xFFE6EBF5);
  static const muted = Color(0xFF7E8AA3);
  static const green = Color(0xFF2ECC71);
  static const red = Color(0xFFFF5B6E);
  static const blue = Color(0xFF4D8DFF);
  static const amber = Color(0xFFFFB020);
}

ThemeData buildTheme() {
  return ThemeData(
    brightness: Brightness.dark,
    scaffoldBackgroundColor: C.bg,
    primaryColor: C.blue,
    colorScheme: const ColorScheme.dark(
      primary: C.blue,
      surface: C.card,
      error: C.red,
    ),
    cardColor: C.card,
    fontFamily: 'Roboto',
    appBarTheme: const AppBarTheme(
      backgroundColor: C.bg,
      elevation: 0,
      centerTitle: false,
    ),
  );
}

Color signColor(num? n) {
  if (n == null) return C.text;
  if (n > 0) return C.green;
  if (n < 0) return C.red;
  return C.text;
}
