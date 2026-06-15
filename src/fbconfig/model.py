"""Protocol-agnostic configuration model (no I/O, no UI).

This is the single source of truth that both the console UI and a future GUI
operate on. Byte offsets are always derived from signal order, so insert/append/
delete keep everything consistent. Existing signals keep their systemTag (UUID);
only newly created signals get a fresh one.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import uuid

from .datatypes import by_sycon, bit_width


@dataclass
class Signal:
    name: str
    sycon_dtype: str            # 'bit','byte','word','real32',...
    array_elements: int = 1     # for 'bit' this is the number of BITS (multiple of 8)
    systemtag: str = ""         # UUID; preserved across edits, generated if empty
    pad_before: int = 0         # free/reserved bytes immediately before this signal
                                # (a gap in the process image). 0 = contiguous.
    signal_type: str = ""       # protocol-specific: 'input'/'output' (EtherNet/IP);
                                # '' for POWERLINK (direction is the Interface).
    bit_offset: int | None = None  # explicit bit address within the direction
                                # (EtherNet/IP, bit-granular); None = derive (POWERLINK).

    def __post_init__(self):
        if not self.systemtag:
            self.systemtag = str(uuid.uuid4())

    @property
    def size(self) -> int:
        """Data bytes consumed, taking arrayElements into account (no padding)."""
        dt = by_sycon(self.sycon_dtype)
        if dt.key == "bit":
            return self.array_elements // 8        # bits -> bytes (8 bit = 1 byte)
        return dt.size * self.array_elements       # element size * count

    @property
    def bits(self) -> int:
        """Width in bits (bit-granular; works for both POWERLINK and EtherNet/IP)."""
        return bit_width(self.sycon_dtype, self.array_elements)

    @property
    def span(self) -> int:
        """Bytes occupied including the reserved gap in front of the signal."""
        return self.pad_before + self.size


@dataclass
class Interface:
    """One direction (In or Out) of the process image."""
    direction: str              # 'In' or 'Out'
    max_bytes: int              # the fixed maximum (INPUT_LENGTH / OUTPUT_LENGTH)
    module_systemtag: str = ""
    signals: list[Signal] = field(default_factory=list)

    # --- byte accounting -------------------------------------------------
    @property
    def used_bytes(self) -> int:
        """Allocated bytes from offset 0 to the end of the last signal,
        INCLUDING reserved gaps (pad_before). free_bytes is the trailing room."""
        return sum(s.span for s in self.signals)

    @property
    def data_bytes(self) -> int:
        """Bytes actually carrying signal data (gaps excluded)."""
        return sum(s.size for s in self.signals)

    @property
    def gap_bytes(self) -> int:
        return sum(s.pad_before for s in self.signals)

    @property
    def free_bytes(self) -> int:
        return self.max_bytes - self.used_bytes

    def byte_offset(self, index: int) -> int:
        """Data start address of signal[index] (accounts for all gaps)."""
        cur = sum(s.span for s in self.signals[:index])
        return cur + self.signals[index].pad_before

    def offset_of(self, sig: Signal) -> int:
        return self.byte_offset(self.signals.index(sig))

    def free_runs(self) -> list[tuple[int, int]]:
        """List of (start_address, length) for every gap and the trailing free
        region — i.e. the unconfigured byte ranges in the process image."""
        runs: list[tuple[int, int]] = []
        cur = 0
        for s in self.signals:
            if s.pad_before:
                runs.append((cur, s.pad_before))
            cur += s.span
        if self.max_bytes > cur:
            runs.append((cur, self.max_bytes - cur))
        return runs

    # --- editing ---------------------------------------------------------
    def can_fit(self, additional_bytes: int) -> bool:
        return additional_bytes <= self.free_bytes

    def insert(self, index: int, sig: Signal) -> None:
        if not self.can_fit(sig.span):
            raise ValueError(
                f"{self.direction}: {sig.span} byte(s) do not fit "
                f"({self.free_bytes} free of {self.max_bytes}).")
        self.signals.insert(index, sig)

    def append(self, sig: Signal) -> None:
        self.insert(len(self.signals), sig)

    def remove(self, index: int) -> Signal:
        return self.signals.pop(index)

    def place_at(self, moved: list[Signal], target_addr: int,
                 leave_gap: bool = False) -> None:
        """Move `moved` signals so the block's first signal starts at byte
        `target_addr`. UUIDs travel with the objects. Raises ValueError if the
        block would not fit into `max_bytes`.

        The block FILLS the free space at the target: it absorbs bytes from the
        gap it is dropped into, so the following signal keeps its address as long
        as the gap is big enough. Only the overflow (block larger than the gap)
        pushes the following signals down.

        leave_gap=True (dropping onto free space) keeps the vacated location
        empty: the moved footprint becomes a reserved gap before the next signal
        so signals after the old spot keep their addresses (nothing shifts up).
        leave_gap=False (dropping onto a signal) collapses the hole = reorder."""
        if not moved:
            return
        moved = list(moved)
        movedset = {id(s) for s in moved}

        # remaining signals + a working pad_before for each. With leave_gap the
        # footprint of each removed signal is reserved before the next signal.
        remaining: list[Signal] = []
        pads: list[int] = []
        pending = 0
        for s in self.signals:
            if id(s) in movedset:
                pending += s.span                  # OLD span (pad not yet changed)
            else:
                pads.append(s.pad_before + (pending if leave_gap else 0))
                remaining.append(s)
                pending = 0
        # trailing `pending` (moved signals were last) -> trailing free, dropped

        # data span of the block measured from its first byte (leading pad excl.)
        block_body = sum(s.size for s in moved) + sum(s.pad_before for s in moved[1:])

        # insertion index + the data-end before it (using the working pads)
        cur = 0
        idx = len(remaining)
        preceding_end = sum(p + s.size for p, s in zip(pads, remaining))
        for j, s in enumerate(remaining):
            data_start = cur + pads[j]
            if target_addr <= data_start:
                idx = j
                preceding_end = cur
                break
            cur = data_start + s.size
        pad_first = max(0, target_addr - preceding_end)

        # absorb the destination gap: the following signal keeps its address
        # unless the block overruns the gap (then it is pushed down by the rest).
        if idx < len(remaining):
            pads[idx] = max(0, pads[idx] - pad_first - block_body)

        new_used = (sum(p + s.size for p, s in zip(pads, remaining))
                    + pad_first + block_body)
        if new_used > self.max_bytes:
            raise ValueError(
                f"{self.direction}: block needs {pad_first + block_body} byte(s) "
                f"at address {target_addr}, but only {self.max_bytes} bytes total "
                f"({new_used - self.max_bytes} over).")

        for s, p in zip(remaining, pads):
            s.pad_before = p
        moved[0].pad_before = pad_first
        self.signals[:] = remaining[:idx] + moved + remaining[idx:]

    def free_run_containing(self, start: int, body: int) -> tuple[int, int] | None:
        """The free run (gap or trailing free) that fully contains [start,
        start+body), or None if that range overlaps a signal or exceeds max_bytes.
        Used to validate 'place new data starting at byte `start`' BEFORE applying."""
        if body <= 0:
            body = 0
        cur = 0
        for s in self.signals:
            if s.pad_before and cur <= start and start + body <= cur + s.pad_before:
                return (cur, s.pad_before)
            cur += s.span
        if cur <= start and start + body <= self.max_bytes:    # trailing free
            return (cur, self.max_bytes - cur)
        return None

    def place_new_at(self, new: list[Signal], start: int) -> None:
        """Insert NEW signals as a block whose first data byte is at `start`. The
        block (its data + any internal bit-packing pads, leading pad excluded) must
        lie entirely in one free run — raises ValueError on overlap/overflow so the
        caller can tell the user to move a signal (drag) or resize. Following
        signals keep their addresses (the block consumes free bytes, never shifts)."""
        if not new:
            return
        body = sum(s.size for s in new) + sum(s.pad_before for s in new[1:])
        run = self.free_run_containing(start, body)
        if run is None:
            raise ValueError(
                f"{self.direction}: {body} byte(s) at address {start} overlap "
                "existing signals or exceed the interface size — move a signal "
                "(drag) or resize the interface first.")
        cur = 0
        for k, s in enumerate(self.signals):
            if s.pad_before and cur <= start and start + body <= cur + s.pad_before:
                new[0].pad_before = start - cur
                s.pad_before = (cur + s.pad_before) - (start + body)   # gap after block
                self.signals[k:k] = new
                return
            cur += s.span
        new[0].pad_before = start - cur                            # trailing free
        self.signals.extend(new)

    # --- bit-granular (EtherNet/IP) ------------------------------------
    @property
    def used_bits(self) -> int:
        if any(s.bit_offset is not None for s in self.signals):
            return max((s.bit_offset + s.bits for s in self.signals), default=0)
        return self.used_bytes * 8

    def repack_bits(self) -> int:
        """Recompute every signal's bit_offset from list order (EtherNet/IP).
        Bits pack tight; multi-byte types are byte-aligned (pad bits to the next
        byte boundary). Returns the used bit count. Raises if it exceeds the
        interface budget (max_bytes * 8)."""
        cur = 0
        for s in self.signals:
            if by_sycon(s.sycon_dtype).key != "bit" and cur % 8:
                cur += 8 - (cur % 8)              # byte-align non-bit types
            s.bit_offset = cur
            cur += s.bits
        if cur > self.max_bytes * 8:
            raise ValueError(
                f"{self.direction}: {cur} bits needed, only {self.max_bytes * 8} "
                f"available ({cur - self.max_bytes * 8} bit over).")
        return cur

    def type_summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.signals:
            k = by_sycon(s.sycon_dtype).key
            out[k] = out.get(k, 0) + 1
        return out


@dataclass
class DeviceInfo:
    protocol: str = ""          # e.g. 'POWERLINK'
    firmware: str = ""
    node_id: Optional[int] = None
    node_name: str = ""         # DNS node name on the card
    ip: str = ""
    vendor_id: Optional[int] = None
    product_code: Optional[int] = None
    base_name: str = ""         # export base name, e.g. 'J207J208'


@dataclass
class ConfigModel:
    device: DeviceInfo
    inp: Interface
    out: Interface
    # opaque per-format payloads needed to write back faithfully (filled by readers)
    raw: dict = field(default_factory=dict)

    def interfaces(self):
        return (self.inp, self.out)
