"""Vrstva mezi GUI a CLI knihovny SpeechScope.

Obrazovky nikdy neskládají příkazovou řádku samy. Argumenty staví
`command.py`, synchronní dotazy (`list`, `doctor`, `models`) dělá
`library.py`, dlouhé běhy (`extract`, `segment`, `transcribe`) řídí
`runner.py` přes `QProcess`.
"""
