"""Adapters for complete batch imports."""

from typing import BinaryIO, Iterable, Iterator

from .decoder import RecordDecoder
from .model import Record


def iter_records(chunks: Iterable[bytes]) -> Iterator[Record]:
    decoder = RecordDecoder()
    for chunk in chunks:
        yield from decoder.feed(chunk)


def read_file(handle: BinaryIO, chunk_size: int = 4096) -> Iterator[Record]:
    """Read from the current position, leaving ownership of handle with the caller."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")

    def chunks():
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk

    yield from iter_records(chunks())
