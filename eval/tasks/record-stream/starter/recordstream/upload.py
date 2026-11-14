"""State held by one live upload request."""

from typing import List

from .decoder import RecordDecoder
from .model import Record


class UploadSession:
    def __init__(self):
        self._decoder = RecordDecoder()
        self._records = []
        self._closed = False

    @property
    def records(self) -> List[Record]:
        """Snapshot of records delivered by successful pushes (and successful close)."""
        return list(self._records)

    def push(self, chunk: bytes) -> int:
        """Publish one successful decoder batch; return the number newly published."""
        if self._closed:
            raise RuntimeError("upload already closed")
        records = self._decoder.feed(chunk)
        self._records.extend(records)
        return len(records)

    def close(self) -> List[Record]:
        """Signal clean EOF; repeated successful calls return equivalent snapshots."""
        self._closed = True
        return self.records
