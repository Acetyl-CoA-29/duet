"""Decoder for the export protocol; see docs/protocol.md for the contract."""

import json
from typing import List

from .model import Record, RecordError


class RecordDecoder:
    def __init__(self):
        self._line = 1
        self._finished = False

    def feed(self, chunk: bytes) -> List[Record]:
        if self._finished:
            raise RuntimeError("decoder already finished")
        try:
            text = chunk.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise RecordError(self._line, "utf8") from None
        records = []
        for line in text.splitlines():
            number = self._line
            self._line += 1
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                raise RecordError(number, "json") from None
            if not isinstance(value, dict):
                raise RecordError(number, "object")
            records.append(Record(number, value))
        return records

    def finish(self) -> List[Record]:
        self._finished = True
        return []
