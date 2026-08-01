"""Resolving a timezone name to something ``zoneinfo`` understands, and
converting timestamps between zones.

An intraday file is written by a Windows process, and Windows names its own
zone "India Standard Time" — not "Asia/Kolkata". ``zoneinfo`` only knows IANA
names, so a name read straight off a file, or typed by a dashboard author who
is used to seeing it in Windows' own Regional Settings dialog, would look
like an invalid timezone to Python and fail every query that touched it. This
module is the one place that translation happens, so the rest of the
codebase can hand any of these spellings to :func:`convert` and not care
which one it got.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from datetime import timezone as _fixed_timezone
from typing import Optional
from zoneinfo import ZoneInfo, available_timezones

import pandas as pd

# A subset of CLDR's windowsZones.xml — the mapping Windows itself ships,
# from its own display names to the IANA zone that carries the same rules.
# Not the full ~140 entries, because nothing here needs, say, every Pacific
# island's standard time; but wide enough that the desks this tool actually
# sees (India, the UK, continental Europe, the US, and the Asia-Pacific
# trading hubs) resolve without a user ever typing an IANA id.
_WINDOWS_TO_IANA: dict[str, str] = {
    "utc": "UTC",
    "india standard time": "Asia/Kolkata",
    "gmt standard time": "Europe/London",
    "greenwich standard time": "Atlantic/Reykjavik",
    "romance standard time": "Europe/Paris",
    "w. europe standard time": "Europe/Berlin",
    "central europe standard time": "Europe/Budapest",
    "central european standard time": "Europe/Warsaw",
    "e. europe standard time": "Europe/Chisinau",
    "fle standard time": "Europe/Kyiv",
    "russian standard time": "Europe/Moscow",
    "turkey standard time": "Europe/Istanbul",
    "israel standard time": "Asia/Jerusalem",
    "egypt standard time": "Africa/Cairo",
    "south africa standard time": "Africa/Johannesburg",
    "morocco standard time": "Africa/Casablanca",
    "arabian standard time": "Asia/Dubai",
    "arabic standard time": "Asia/Baghdad",
    "west asia standard time": "Asia/Tashkent",
    "pakistan standard time": "Asia/Karachi",
    "bangladesh standard time": "Asia/Dhaka",
    "myanmar standard time": "Asia/Yangon",
    "se asia standard time": "Asia/Bangkok",
    "china standard time": "Asia/Shanghai",
    "taipei standard time": "Asia/Taipei",
    "singapore standard time": "Asia/Singapore",
    "north asia standard time": "Asia/Krasnoyarsk",
    "ulaanbaatar standard time": "Asia/Ulaanbaatar",
    "north korea standard time": "Asia/Pyongyang",
    "korea standard time": "Asia/Seoul",
    "tokyo standard time": "Asia/Tokyo",
    "aus eastern standard time": "Australia/Sydney",
    "aus central standard time": "Australia/Darwin",
    "cen. australia standard time": "Australia/Adelaide",
    "e. australia standard time": "Australia/Brisbane",
    "tasmania standard time": "Australia/Hobart",
    "new zealand standard time": "Pacific/Auckland",
    "fiji standard time": "Pacific/Fiji",
    "samoa standard time": "Pacific/Apia",
    "central pacific standard time": "Pacific/Guadalcanal",
    "hawaiian standard time": "Pacific/Honolulu",
    "alaskan standard time": "America/Anchorage",
    "pacific standard time": "America/Los_Angeles",
    "mountain standard time": "America/Denver",
    "us mountain standard time": "America/Phoenix",
    "central standard time": "America/Chicago",
    "canada central standard time": "America/Regina",
    "eastern standard time": "America/New_York",
    "atlantic standard time": "America/Halifax",
    "sa pacific standard time": "America/Bogota",
    "venezuela standard time": "America/Caracas",
    "sa eastern standard time": "America/Cayenne",
    "central brazilian standard time": "America/Cuiaba",
    "e. south america standard time": "America/Sao_Paulo",
    "argentina standard time": "America/Buenos_Aires",
    "montevideo standard time": "America/Montevideo",
    "greenland standard time": "America/Godthab",
    "azores standard time": "Atlantic/Azores",
    "cape verde standard time": "Atlantic/Cape_Verde",
}

# Bare abbreviations a person types without thinking of it as a "Windows
# name" at all. Several of these are genuinely ambiguous in the wild (CST is
# China, US Central, or Cuba depending on who wrote it) — the choices below
# favour the reading most likely at a trading desk that already has India,
# the UK, continental Europe, the US and the Asia-Pacific hubs in its files.
_ABBREVIATIONS: dict[str, str] = {
    "utc": "UTC",
    "gmt": "UTC",
    "ist": "Asia/Kolkata",
    "cet": "Europe/Berlin",
    "cest": "Europe/Berlin",
    "jst": "Asia/Tokyo",
    "kst": "Asia/Seoul",
    "sgt": "Asia/Singapore",
    "hkt": "Asia/Hong_Kong",
    "est": "America/New_York",
    "edt": "America/New_York",
    "cst": "America/Chicago",
    "pst": "America/Los_Angeles",
}

# "UTC+05:30", "+05:30", "UTC+0530", case-insensitive, spaces allowed around
# the sign. Minutes are optional (colon or not) so "+05" and "UTC+5" resolve
# too, on the same footing as the fully-punctuated forms.
_OFFSET_RE = re.compile(r"^(?:UTC)?\s*([+-])\s*(\d{1,2}):?(\d{2})?$",
                        re.IGNORECASE)

# What to_iana returns for a literal offset — not a real IANA id, but a
# recognisable shape that convert()'s tzinfo resolution below knows to build
# a fixed-offset zone from rather than looking up in the tz database.
_RESOLVED_OFFSET_RE = re.compile(r"^UTC([+-])(\d{2}):(\d{2})$")


def _parse_offset(text: str) -> Optional[timedelta]:
    m = _OFFSET_RE.match(text)
    if not m:
        return None
    sign, hours, minutes = m.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes or 0))
    return -delta if sign == "-" else delta


def _format_offset(delta: timedelta) -> str:
    total = int(delta.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"UTC{sign}{total // 60:02d}:{total % 60:02d}"


def to_iana(name: str) -> str:
    """Resolve a Windows display name, IANA id, abbreviation or UTC offset.

    Exports name a zone the way Windows does, because that is what the
    exporting process's own regional settings say — not the way the IANA
    database does, and not the way a person typing a dashboard config does
    either, so all three spellings are accepted here rather than forcing
    every caller to normalise first.
    """
    if not isinstance(name, str):
        raise ValueError(f"to_iana: unrecognised timezone {name!r}")
    key = name.strip()
    if not key:
        raise ValueError("to_iana: no timezone given")
    lowered = key.lower()

    if lowered in _WINDOWS_TO_IANA:
        return _WINDOWS_TO_IANA[lowered]
    if key in available_timezones():
        return key
    if lowered in _ABBREVIATIONS:
        return _ABBREVIATIONS[lowered]
    offset = _parse_offset(key)
    if offset is not None:
        return _format_offset(offset)
    raise ValueError(
        f"to_iana: unrecognised timezone '{name}' — not a Windows display "
        "name, IANA id, known abbreviation or UTC offset")


def _tzinfo_for(resolved: str):
    """The tzinfo behind an already-resolved name from :func:`to_iana`.

    Most resolved names are real IANA ids and go straight to ZoneInfo, which
    is what makes DST correct at each timestamp rather than fixed for the
    whole column. A literal offset has no IANA id to look up — "UTC+05:30" is
    not a place with a rulebook, just a fixed shift — so it gets a plain
    fixed-offset tzinfo instead.
    """
    m = _RESOLVED_OFFSET_RE.match(resolved)
    if m:
        sign, hours, minutes = m.groups()
        delta = timedelta(hours=int(hours), minutes=int(minutes))
        return _fixed_timezone(delta if sign == "+" else -delta)
    return ZoneInfo(resolved)


def iana_names() -> list[str]:
    """Every IANA zone id, sorted — the list a picker offers.

    The other spellings :func:`to_iana` accepts exist because a *file* names
    its zone the way the machine that wrote it does. Somebody choosing a zone
    is not in that position: they should be offered the canonical names and
    nothing else, so that what is stored is unambiguous wherever it is read.
    """
    return sorted(available_timezones())


def local_iana() -> str:
    """The machine's own zone as an IANA id, or UTC if it cannot be named.

    :func:`local_zone` answers in whatever spelling the machine uses — a
    Windows display name, or a bare offset where even that is unavailable. A
    bare offset is not a zone (it has no rulebook, so it cannot know its own
    daylight saving), so rather than offer one as if it were, this falls back
    to UTC and lets the person choose.
    """
    try:
        resolved = to_iana(local_zone())
    except ValueError:
        return "UTC"
    return resolved if resolved in available_timezones() else "UTC"


def tzinfo_for(name: str):
    """The tzinfo behind any spelling :func:`to_iana` accepts.

    Callers that work in datetimes rather than in pandas columns — an alert's
    active hours, say — need the zone itself rather than a converted series.
    """
    return _tzinfo_for(to_iana(name))


def convert(series: pd.Series, from_zone: str, to_zone: str) -> pd.Series:
    """Reinterpret a column of naive timestamps in another zone.

    Buckets are localised to ``from_zone``, moved to ``to_zone`` — recomputing
    the DST offset at each row's own date rather than applying one offset to
    the whole column, which is the difference between a July bucket and a
    January one landing an hour apart the way they actually would — and then
    stripped back to a naive timestamp, so the result prints as a plain local
    time instead of repeating the same offset in every cell of a table.

    An hour that is skipped or repeated at a DST boundary (a bucket that,
    read in ``from_zone``, names a moment that either never happened or
    happened twice) is turned into a null rather than guessed at: the same
    choice this codebase already makes for a ratio with no answer (an
    infinity is not a figure to report), because picking one of two possible
    instants silently would be wrong exactly as often as it was right.
    """
    from_tz = _tzinfo_for(to_iana(from_zone))
    to_tz = _tzinfo_for(to_iana(to_zone))
    if series.empty:
        return series.copy()
    localized = series.dt.tz_localize(
        from_tz, ambiguous="NaT", nonexistent="NaT")
    converted = localized.dt.tz_convert(to_tz)
    return converted.dt.tz_localize(None)


def day_offset(before: pd.Series, after: pd.Series) -> pd.Series:
    """Signed whole days between two naive timestamp series' calendar dates.

    A bucket that lands on a different calendar day after conversion is easy
    to misread as still belonging to the session it came from — 09:15 in
    Mumbai is 23:45 the day *before* in New York — so callers get this as a
    plain -1/0/+1 column to flag alongside the converted time rather than
    have to work it out again themselves.
    """
    return (after.dt.normalize() - before.dt.normalize()).dt.days


def local_zone() -> str:
    """Best-effort name for the machine's own zone, for a ``to: "local"``.

    Windows itself answers this question through the registry, in exactly
    the display-name spelling :func:`to_iana` already translates — asking it
    there avoids a dependency (``tzlocal``) this project would otherwise need
    only for this one lookup. Off Windows, or if the registry key cannot be
    read, fall back to the process's current UTC offset: not DST-correct for
    every date in a file (a fixed offset does not know when the machine's own
    zone falls back), but a usable answer on a machine this module cannot
    otherwise introspect, rather than refusing to run at all.
    """
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation") as k:
            name, _ = winreg.QueryValueEx(k, "TimeZoneKeyName")
            if name:
                return name
    except (ImportError, OSError):
        pass
    offset = datetime.now().astimezone().utcoffset() or timedelta()
    return _format_offset(offset)
