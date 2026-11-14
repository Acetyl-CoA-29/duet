"""Decoder for the export protocol; see docs/protocol.md for the contract."""

import json
from typing import List

from .model import Record, RecordError


class RecordDecoder:
    def __init__(self):
        self._line = 1
        self._finished = False
        self._buffer = b""

    def _record(self, raw):
        number = self._line
        self._line += 1
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise RecordError(number, "utf8") from None
        if not text.strip():
            return None
        try:
            value = json.loads(text)
        except ValueError:
            raise RecordError(number, "json") from None
        if not isinstance(value, dict):
            raise RecordError(number, "object")
        return Record(number, value)

    def feed(self, chunk: bytes) -> List[Record]:
        if self._finished:
            raise RuntimeError("decoder already finished")
        lines = (self._buffer + chunk).split(b"\n")
        self._buffer = lines.pop()
        records = []
        for line in lines:
            record = self._record(line)
            if record is not None:
                records.append(record)
        return records

    def finish(self) -> List[Record]:
        if self._finished:
            return []
        self._finished = True
        if not self._buffer:
            return []
        record = self._record(self._buffer)
        self._buffer = b""
        return [] if record is None else [record]
