"""Decoder for the export protocol; see docs/protocol.md for the contract."""

import json
from typing import List, Optional

from .model import Record, RecordError


class RecordDecoder:
    def __init__(self):
        self._line = 1
        self._finished = False
        self._failed = None
        self._buffer = b""

    def _guard(self):
        if self._failed is not None:
            raise self._failed

    def _record(self, raw: bytes) -> Optional[Record]:
        number = self._line
        self._line += 1
        if number == 1 and raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise RecordError(number, "utf8") from None
        if not text.strip(" \t\r"):
            return None
        try:
            value = json.loads(text)
        except ValueError:
            raise RecordError(number, "json") from None
        if not isinstance(value, dict):
            raise RecordError(number, "object")
        return Record(number, value)

    def feed(self, chunk: bytes) -> List[Record]:
        self._guard()
        if self._finished:
            raise RuntimeError("decoder already finished")
        lines = (self._buffer + chunk).split(b"\n")
        self._buffer = lines.pop()
        records = []
        try:
            for line in lines:
                record = self._record(line)
                if record is not None:
                    records.append(record)
        except RecordError as exc:
            self._failed = exc
            self._buffer = b""
            raise
        return records

    def finish(self) -> List[Record]:
        self._guard()
        if self._finished:
            return []
        records = []
        try:
            if self._buffer:
                record = self._record(self._buffer)
                if record is not None:
                    records.append(record)
        except RecordError as exc:
            self._failed = exc
            self._buffer = b""
            raise
        self._buffer = b""
        self._finished = True
        return records
