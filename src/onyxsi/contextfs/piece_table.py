"""
Memoryview Piece Table Buffer Engine & Nano-Checkpoints.
Zero-copy document representation using original buffer memoryview and append-only add buffer.
Supports sub-millisecond range slices, O(log N) bisect piece lookup over prefix sums,
automatic coalescing (>512 pieces), and bidirectional microsecond NanoCheckpoints rollback.
"""

import bisect
import time
from dataclasses import dataclass
from typing import List, Tuple, Union
from onyxsi.contracts import OnyxErrorCode, OnyxError

BUFFER_ORIG = 0
BUFFER_ADD = 1
MAX_PIECES_THRESHOLD = 512


@dataclass(slots=True, frozen=True)
class PieceRecord:
    """A single span inside the Piece Table pointing to either orig_buf or add_buf."""
    buffer_type: int  # BUFFER_ORIG (0) or BUFFER_ADD (1)
    start: int
    length: int


@dataclass(slots=True, frozen=True)
class NanoCheckpoint:
    """Zero-allocation snapshot of Piece Table topology for instant bidirectional rollback."""
    checkpoint_id: int
    timestamp: float
    pieces: Tuple[PieceRecord, ...]
    add_buf_len: int


class PieceTableDocument:
    """
    High-performance Piece Table text/code document engine.
    Zero-copy inserts, deletes, and replaces with bisect-driven O(log N) piece lookup.
    """

    def __init__(self, initial_content: Union[str, bytes] = b""):
        if isinstance(initial_content, str):
            self._orig_bytes = initial_content.encode("utf-8")
        else:
            self._orig_bytes = bytes(initial_content)

        self._orig_view = memoryview(self._orig_bytes)
        self._add_buf = bytearray()
        self._pieces: List[PieceRecord] = []
        self._prefix_sums: List[int] = []
        if len(self._orig_bytes) > 0:
            self._pieces.append(PieceRecord(buffer_type=BUFFER_ORIG, start=0, length=len(self._orig_bytes)))
            self._prefix_sums.append(0)

        self._checkpoints: List[NanoCheckpoint] = []
        self._next_checkpoint_id = 1
        self._cached_length = len(self._orig_bytes)

    def total_length(self) -> int:
        return self._cached_length

    def insert(self, pos: int, data: Union[str, bytes]) -> None:
        """Inserts text or bytes at byte position pos."""
        b_data = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        if not b_data:
            return

        if pos < 0 or pos > self._cached_length:
            raise OnyxError(OnyxErrorCode.E2004, f"Insert position out of bounds: {pos} (total: {self._cached_length})")

        add_start = len(self._add_buf)
        self._add_buf.extend(b_data)
        new_piece = PieceRecord(buffer_type=BUFFER_ADD, start=add_start, length=len(b_data))

        if not self._pieces:
            self._pieces.append(new_piece)
            self._rebuild_index()
            return

        piece_idx, offset_in_piece = self._find_piece_at_offset(pos)

        if piece_idx == len(self._pieces):
            prev = self._pieces[-1]
            if prev.buffer_type == BUFFER_ADD and (prev.start + prev.length == add_start):
                self._pieces[-1] = PieceRecord(BUFFER_ADD, prev.start, prev.length + len(b_data))
            else:
                self._pieces.append(new_piece)
        else:
            target = self._pieces[piece_idx]
            if offset_in_piece == 0:
                if piece_idx > 0:
                    prev = self._pieces[piece_idx - 1]
                    if prev.buffer_type == BUFFER_ADD and (prev.start + prev.length == add_start):
                        self._pieces[piece_idx - 1] = PieceRecord(BUFFER_ADD, prev.start, prev.length + len(b_data))
                        self._rebuild_index()
                        return
                self._pieces.insert(piece_idx, new_piece)
            elif offset_in_piece == target.length:
                if target.buffer_type == BUFFER_ADD and (target.start + target.length == add_start):
                    self._pieces[piece_idx] = PieceRecord(BUFFER_ADD, target.start, target.length + len(b_data))
                else:
                    self._pieces.insert(piece_idx + 1, new_piece)
            else:
                left = PieceRecord(buffer_type=target.buffer_type, start=target.start, length=offset_in_piece)
                right = PieceRecord(
                    buffer_type=target.buffer_type,
                    start=target.start + offset_in_piece,
                    length=target.length - offset_in_piece,
                )
                self._pieces[piece_idx:piece_idx + 1] = [left, new_piece, right]

        self._rebuild_index()
        if len(self._pieces) > MAX_PIECES_THRESHOLD:
            self.coalesce()

    def delete(self, pos: int, length: int) -> None:
        """Deletes length bytes starting at position pos."""
        if length <= 0:
            return
        if pos < 0 or pos + length > self._cached_length:
            raise OnyxError(OnyxErrorCode.E2004, f"Delete range out of bounds: {pos}..{pos + length} (total: {self._cached_length})")

        rem = length
        curr_pos = pos

        while rem > 0 and self._pieces:
            piece_idx, offset_in_piece = self._find_piece_at_offset(curr_pos)
            if piece_idx >= len(self._pieces):
                break

            target = self._pieces[piece_idx]
            avail = target.length - offset_in_piece
            del_len = min(rem, avail)

            if offset_in_piece == 0 and del_len == target.length:
                del self._pieces[piece_idx]
            elif offset_in_piece == 0:
                self._pieces[piece_idx] = PieceRecord(
                    buffer_type=target.buffer_type,
                    start=target.start + del_len,
                    length=target.length - del_len,
                )
            elif offset_in_piece + del_len == target.length:
                self._pieces[piece_idx] = PieceRecord(
                    buffer_type=target.buffer_type,
                    start=target.start,
                    length=offset_in_piece,
                )
            else:
                left = PieceRecord(buffer_type=target.buffer_type, start=target.start, length=offset_in_piece)
                right = PieceRecord(
                    buffer_type=target.buffer_type,
                    start=target.start + offset_in_piece + del_len,
                    length=target.length - (offset_in_piece + del_len),
                )
                self._pieces[piece_idx:piece_idx + 1] = [left, right]

            rem -= del_len
            self._rebuild_index()

        if len(self._pieces) > MAX_PIECES_THRESHOLD:
            self.coalesce()

    def replace(self, pos: int, length: int, data: Union[str, bytes]) -> None:
        """Replaces length bytes at position pos with data."""
        self.delete(pos, length)
        self.insert(pos, data)

    def get_bytes(self) -> bytes:
        """Renders entire document to contiguous bytes."""
        chunks = []
        for p in self._pieces:
            if p.buffer_type == BUFFER_ORIG:
                chunks.append(self._orig_view[p.start:p.start + p.length].tobytes())
            else:
                chunks.append(bytes(self._add_buf[p.start:p.start + p.length]))
        return b"".join(chunks)

    def get_text(self) -> str:
        """Renders entire document to string."""
        return self.get_bytes().decode("utf-8", errors="replace")

    def get_slice(self, start: int, length: int) -> bytes:
        """Extracts a slice [start : start + length] from the Piece Table."""
        if start < 0 or start + length > self._cached_length:
            raise OnyxError(OnyxErrorCode.E2004, f"Slice out of bounds: {start}..{start + length} (total: {self._cached_length})")

        res = []
        rem = length
        curr = start

        while rem > 0:
            piece_idx, offset = self._find_piece_at_offset(curr)
            if piece_idx >= len(self._pieces):
                break
            target = self._pieces[piece_idx]
            take = min(rem, target.length - offset)
            if target.buffer_type == BUFFER_ORIG:
                res.append(self._orig_view[target.start + offset:target.start + offset + take].tobytes())
            else:
                res.append(bytes(self._add_buf[target.start + offset:target.start + offset + take]))
            rem -= take
            curr += take

        return b"".join(res)

    def get_line_range(self, start_line: int, end_line: int) -> str:
        """Extracts lines [start_line..end_line] (1-indexed, inclusive)."""
        text = self.get_text()
        lines = text.splitlines(keepends=True)
        s_idx = max(0, start_line - 1)
        e_idx = min(len(lines), end_line)
        return "".join(lines[s_idx:e_idx])

    def create_checkpoint(self) -> int:
        """Creates a NanoCheckpoint snapshot for sub-millisecond rollback."""
        cid = self._next_checkpoint_id
        self._next_checkpoint_id += 1
        cp = NanoCheckpoint(
            checkpoint_id=cid,
            timestamp=time.time(),
            pieces=tuple(self._pieces),
            add_buf_len=len(self._add_buf),
        )
        self._checkpoints.append(cp)
        return cid

    def rollback(self, checkpoint_id: int) -> bool:
        """
        Rolls back or forward document state to checkpoint_id in microsecond time.
        Preserves append-only _add_buf so any checkpoint can be visited during Causal Bisect.
        """
        target_cp = None
        for cp in reversed(self._checkpoints):
            if cp.checkpoint_id == checkpoint_id:
                target_cp = cp
                break

        if target_cp is None:
            return False

        self._pieces = list(target_cp.pieces)
        self._rebuild_index()
        return True

    def coalesce(self) -> None:
        """Coalesces adjacent contiguous pieces to keep piece table compact (<512 pieces)."""
        if not self._pieces:
            self._rebuild_index()
            return

        coalesced: List[PieceRecord] = []
        curr = self._pieces[0]

        for nxt in self._pieces[1:]:
            if (curr.buffer_type == nxt.buffer_type) and (curr.start + curr.length == nxt.start):
                curr = PieceRecord(
                    buffer_type=curr.buffer_type,
                    start=curr.start,
                    length=curr.length + nxt.length,
                )
            else:
                coalesced.append(curr)
                curr = nxt
        coalesced.append(curr)

        if len(coalesced) > MAX_PIECES_THRESHOLD and not self._checkpoints:
            flattened = self.get_bytes()
            self._orig_bytes = flattened
            self._orig_view = memoryview(self._orig_bytes)
            self._add_buf.clear()
            self._pieces = [PieceRecord(buffer_type=BUFFER_ORIG, start=0, length=len(flattened))] if flattened else []
        else:
            self._pieces = coalesced

        self._rebuild_index()

    def _find_piece_at_offset(self, pos: int) -> Tuple[int, int]:
        """Finds (piece_index, offset_within_piece) in O(log N) using bisect on prefix sums."""
        if not self._pieces or pos >= self._cached_length:
            return len(self._pieces), 0
        idx = bisect.bisect_right(self._prefix_sums, pos) - 1
        if idx < 0:
            return 0, 0
        piece_start_offset = self._prefix_sums[idx]
        return idx, pos - piece_start_offset

    def _rebuild_index(self) -> None:
        """Rebuilds the prefix sum index for O(log N) binary search."""
        self._prefix_sums.clear()
        acc = 0
        for p in self._pieces:
            self._prefix_sums.append(acc)
            acc += p.length
        self._cached_length = acc
