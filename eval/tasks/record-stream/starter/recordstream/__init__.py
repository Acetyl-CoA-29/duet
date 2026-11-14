"""Public interface for record-stream ingest."""

from .decoder import RecordDecoder
from .model import Record, RecordError
from .readers import iter_records, read_file
from .report import summarize
from .upload import UploadSession

__all__ = ["Record", "RecordError", "RecordDecoder", "iter_records",
           "read_file", "summarize", "UploadSession"]
