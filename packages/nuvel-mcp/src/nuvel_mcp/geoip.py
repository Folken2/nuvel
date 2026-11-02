"""Country, never IP: an optional IP-range -> country table for usage events.

``NUVEL_MCP_GEOIP_CSV`` names a country-level IP-range CSV in the DB-IP
"IP to Country Lite" format (``start_ip,end_ip,country_code``; IPv4 and IPv6;
plain or ``.gz``). It is downloaded by the operator from
https://db-ip.com/db/download/ip-to-country-lite and is licensed CC BY 4.0,
which requires attribution ("IP Geolocation by DB-IP", linking db-ip.com)
wherever the data is shown. nuvel-mcp never downloads or ships it.

The table is loaded once at startup into sorted ranges and searched with
:mod:`bisect`. The HTTP transport looks the caller's address up per request
and puts only the two-letter ISO code on the usage event; the address is
dropped right there, never stored or logged (not even hashed).
``NUVEL_MCP_RECORD_COUNTRY=0`` turns the lookup off even with a CSV.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import io
import ipaddress
import os
import re
import sys
from array import array
from pathlib import Path

CSV_ENV = "NUVEL_MCP_GEOIP_CSV"
RECORD_ENV = "NUVEL_MCP_RECORD_COUNTRY"
_COUNTRY = re.compile(r"\A[A-Z]{2}\Z")
#: Codes that name no country (DB-IP marks unassigned ranges ZZ).
_NOT_A_COUNTRY = frozenset({"ZZ", "XX"})


class _Ranges:
    """Sorted, non-overlapping ``[start, end] -> code`` ranges of one IP version."""

    def __init__(self, rows: list[tuple[int, int, str]], typecode: str | None):
        rows.sort()
        merged: list[list] = []
        for start, end, code in rows:
            if merged and merged[-1][2] == code and merged[-1][1] + 1 == start:
                merged[-1][1] = end
            else:
                merged.append([start, end, code])
        # IPv4 fits an unsigned 64-bit array (compact); IPv6 needs Python ints.
        make = (lambda xs: array(typecode, xs)) if typecode else list
        self.starts = make([r[0] for r in merged])
        self.ends = make([r[1] for r in merged])
        self.codes = [sys.intern(r[2]) for r in merged]

    def __len__(self) -> int:
        return len(self.codes)

    def find(self, value: int) -> str | None:
        i = bisect.bisect_right(self.starts, value) - 1
        if i >= 0 and value <= self.ends[i]:
            return self.codes[i]
        return None


class CountryTable:
    """``lookup(ip) -> "DE"`` or ``None``; callable as ``table(ip)``."""

    def __init__(self, v4: _Ranges, v6: _Ranges, skipped: int = 0):
        self._v4, self._v6 = v4, v6
        #: Rows that were not ``ip,ip,CC`` with start <= end, same version.
        self.skipped = skipped

    def __len__(self) -> int:
        return len(self._v4) + len(self._v6)

    def __call__(self, ip: str) -> str | None:
        return self.lookup(ip)

    def lookup(self, ip: str) -> str | None:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        ranges = self._v4 if address.version == 4 else self._v6
        return ranges.find(int(address))

    @classmethod
    def load(cls, path) -> "CountryTable":
        """Parse a DB-IP country CSV (``.gz`` too). Raises ``ValueError`` when
        the file cannot be read or holds no valid row."""
        path = Path(path).expanduser()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"{CSV_ENV}: cannot read {path}: {exc.strerror or exc}") from None
        if path.suffix == ".gz":
            try:
                raw = gzip.decompress(raw)
            except (OSError, EOFError) as exc:
                raise ValueError(f"{CSV_ENV}: {path} is not valid gzip: {exc}") from None
        rows: dict[int, list] = {4: [], 6: []}
        skipped = 0
        for row in csv.reader(io.StringIO(raw.decode("utf-8", errors="replace"))):
            if not row:
                continue
            try:
                start = ipaddress.ip_address(row[0].strip())
                end = ipaddress.ip_address(row[1].strip())
                code = row[2].strip()
            except (ValueError, IndexError):
                skipped += 1
                continue
            if start.version != end.version or start > end or not _COUNTRY.fullmatch(code):
                skipped += 1
                continue
            if code in _NOT_A_COUNTRY:
                continue
            rows[start.version].append((int(start), int(end), code))
        if not rows[4] and not rows[6]:
            raise ValueError(f"{CSV_ENV}: {path} has no valid start_ip,end_ip,country rows "
                             "(expected the DB-IP 'IP to Country Lite' CSV)")
        return cls(_Ranges(rows[4], "Q"), _Ranges(rows[6], None), skipped)


def country_lookup_from_env(environ=None) -> CountryTable | None:
    """The table named by ``NUVEL_MCP_GEOIP_CSV``, or ``None`` when it is
    unset or ``NUVEL_MCP_RECORD_COUNTRY`` is off. Raises ``ValueError``."""
    environ = os.environ if environ is None else environ
    path = environ.get(CSV_ENV, "").strip()
    if not path:
        return None
    flag = environ.get(RECORD_ENV, "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return None
    if flag not in ("", "1", "true", "yes", "on"):
        raise ValueError(f"{RECORD_ENV} must be 0 or 1")
    return CountryTable.load(path)
