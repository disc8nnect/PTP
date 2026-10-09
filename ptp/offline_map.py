"""Offline street map: download one area once while online, then show it with Wi-Fi off.

The map is OpenStreetMap data as vector tiles, from the Protomaps daily build: one large
PMTiles file on the web (https://docs.protomaps.com/basemaps/downloads). `download` reads only
the tiles of one area from it with HTTP range requests and saves them in an MBTiles file (an
SQLite database), which the app serves to the map on the Nearby screen.

    python3 -m ptp.offline_map download --lat 14.85 --lon 120.81      # once, with internet
    python3 -m ptp.offline_map info                                    # what is downloaded

Two areas are saved: street detail (zoom 15) within --km of the point, and a wider, less
detailed area (zoom 11) within --context-km so the map still makes sense zoomed out. The map
library draws zoom 16 and closer from the zoom 15 tiles.

PMTiles format: https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md
MBTiles format: https://github.com/mapbox/mbtiles-spec/blob/master/1.3/spec.md
Standard library only.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
import os
import sqlite3
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "map" / "area.mbtiles"
BUILDS = "https://build.protomaps.com/{day}.pmtiles"
USER_AGENT = "PTP-offline-map/1.0 (pregnancy app; one-time area download)"

STYLE_SCHEMA = "4"                     # web/vendor/map/style.json is @protomaps/basemaps 5.7.2: tiles v4
COMPRESSION = {1: "none", 2: "gzip"}  # PMTiles codes we can serve to a browser as they are
MERGE_GAP = 32 * 1024                 # read neighbouring tiles in one request if this close
MAX_REQUEST = 8 * 1024 * 1024


def map_path() -> Path:
    return Path(os.environ.get("PTP_MAP") or DEFAULT_PATH)


def facilities_path() -> Path:
    """Facilities found in the downloaded map are saved next to it."""
    return map_path().with_name("facilities.json")


# ----------------------------------------------------------------------------- tile maths


def tile_id(z: int, x: int, y: int) -> int:
    """PMTiles tile ID: tiles of lower zooms first, then position on a Hilbert curve."""
    acc = ((1 << (2 * z)) - 1) // 3
    n = 1 << z
    d = 0
    s = n >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 1 << z
    lat = max(-85.0511, min(85.0511, lat))
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_to_lonlat(z: int, x: float, y: float) -> tuple[float, float]:
    """Longitude and latitude of a point given in tile units (x, y may have fractions)."""
    n = 1 << z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def area_bbox(lat: float, lon: float, km: float) -> tuple[float, float, float, float]:
    """West, south, east, north of a square about 2 x km wide centred on the point."""
    dlat = km / 110.574
    dlon = km / (111.320 * max(0.01, math.cos(math.radians(lat))))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def tiles_in_bbox(bbox: tuple[float, float, float, float], z: int) -> list[tuple[int, int, int]]:
    west, south, east, north = bbox
    x0, y0 = lonlat_to_tile(west, north, z)
    x1, y1 = lonlat_to_tile(east, south, z)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


# ----------------------------------------------------------------------------- PMTiles reading


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, pos
        shift += 7


def parse_directory(buf: bytes) -> list[tuple[int, int, int, int]]:
    """Entries (tile_id, offset, length, run_length); run_length 0 points to a leaf directory."""
    n, pos = _varint(buf, 0)
    ids, runs, lengths, offsets = [], [], [], []
    last = 0
    for _ in range(n):
        delta, pos = _varint(buf, pos)
        last += delta
        ids.append(last)
    for _ in range(n):
        v, pos = _varint(buf, pos)
        runs.append(v)
    for _ in range(n):
        v, pos = _varint(buf, pos)
        lengths.append(v)
    for i in range(n):
        v, pos = _varint(buf, pos)
        offsets.append(offsets[i - 1] + lengths[i - 1] if v == 0 and i > 0 else v - 1)
    return list(zip(ids, offsets, lengths, runs))


class PMTiles:
    """Reads tiles from a PMTiles archive through read(offset, length) -> bytes."""

    def __init__(self, read: Callable[[int, int], bytes]):
        self.read = read
        start = read(0, 16384)
        if start[:7] != b"PMTiles" or start[7] != 3:
            raise ValueError("not a PMTiles version 3 file")
        (self.root_offset, self.root_length, self.metadata_offset, self.metadata_length,
         self.leaf_offset, self.leaf_length, self.data_offset, self.data_length) = struct.unpack_from("<8Q", start, 8)
        (_, self.internal_compression, self.tile_compression, self.tile_type,
         self.min_zoom, self.max_zoom) = struct.unpack_from("<6B", start, 96)
        if self.internal_compression not in COMPRESSION:
            raise ValueError(f"unsupported directory compression {self.internal_compression}")
        self._start = start
        self._dirs: dict[tuple[int, int], list] = {}
        self._lock = threading.Lock()

    def _decompress(self, data: bytes) -> bytes:
        return gzip.decompress(data) if self.internal_compression == 2 else data

    def _directory(self, offset: int, length: int) -> list:
        key = (offset, length)
        with self._lock:
            if key in self._dirs:
                return self._dirs[key]
        if offset + length <= len(self._start):
            raw = self._start[offset:offset + length]
        else:
            raw = self.read(offset, length)
        entries = parse_directory(self._decompress(raw))
        with self._lock:
            self._dirs[key] = entries
        return entries

    def metadata(self) -> dict:
        if not self.metadata_length:
            return {}
        raw = self.read(self.metadata_offset, self.metadata_length)
        return json.loads(self._decompress(raw))

    def locate(self, z: int, x: int, y: int) -> tuple[int, int] | None:
        """Absolute (offset, length) of a tile's bytes, or None if the archive has no such tile."""
        tid = tile_id(z, x, y)
        offset, length = self.root_offset, self.root_length
        for _ in range(4):  # the spec allows at most 3 levels of leaf directories
            entries = self._directory(offset, length)
            i = bisect.bisect_right([e[0] for e in entries], tid) - 1
            if i < 0:
                return None
            first, off, ln, run = entries[i]
            if run == 0:  # a leaf directory covers this range of tile IDs
                offset, length = self.leaf_offset + off, ln
                continue
            return (self.data_offset + off, ln) if tid < first + run else None
        return None


def file_reader(path: Path) -> Callable[[int, int], bytes]:
    lock = threading.Lock()
    fh = open(path, "rb")

    def read(offset: int, length: int) -> bytes:
        with lock:
            fh.seek(offset)
            return fh.read(length)
    return read


def http_reader(url: str, timeout: float = 60) -> Callable[[int, int], bytes]:
    def read(offset: int, length: int) -> bytes:
        req = urllib.request.Request(url, headers={"Range": f"bytes={offset}-{offset + length - 1}",
                                                   "User-Agent": USER_AGENT})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status != 206:  # a server that ignores Range would send the whole planet
                        raise ValueError(f"{url} does not support range requests (HTTP {resp.status})")
                    data = resp.read()
                if len(data) != length:
                    raise OSError(f"short read: {len(data)} of {length} bytes")
                return data
            except (urllib.error.URLError, OSError, TimeoutError):
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")
    return read


def latest_build(today: date | None = None, days: int = 14) -> str:
    """URL of the newest Protomaps daily build that exists (they are kept for a few days)."""
    today = today or date.today()
    for back in range(days):
        url = BUILDS.format(day=(today - timedelta(days=back)).strftime("%Y%m%d"))
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    return url
        except (urllib.error.URLError, OSError):
            continue
    raise OSError("No recent map build found at build.protomaps.com. Are you online? "
                  "You can also pass --source with a .pmtiles URL or file.")


def open_source(source: str) -> PMTiles:
    if source.startswith(("http://", "https://")):
        return PMTiles(http_reader(source))
    return PMTiles(file_reader(Path(source)))


# ----------------------------------------------------------------------------- extract


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Group sorted (offset, length) spans into fewer, larger reads."""
    out: list[list[int]] = []
    for off, ln in sorted(spans):
        if out and off - (out[-1][0] + out[-1][1]) <= MERGE_GAP and off + ln - out[-1][0] <= MAX_REQUEST:
            out[-1][1] = max(out[-1][1], off + ln - out[-1][0])
        else:
            out.append([off, ln])
    return [(a, b) for a, b in out]


def extract(pm: PMTiles, out: Path, detail: tuple, context: tuple, detail_zoom: int = 15,
            context_zoom: int = 11, source: str = "", progress: Callable[[str], None] = print) -> dict:
    """Save the tiles of two areas to an MBTiles file. Returns a short report."""
    if pm.tile_compression not in COMPRESSION:
        raise ValueError(f"unsupported tile compression {pm.tile_compression}")
    detail_zoom = min(detail_zoom, pm.max_zoom)
    context_zoom = min(context_zoom, detail_zoom)
    wanted = []
    for z in range(pm.min_zoom, detail_zoom + 1):
        wanted += tiles_in_bbox(context if z <= context_zoom else detail, z)
    progress(f"Finding {len(wanted)} map tiles...")
    found = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for zxy, span in zip(wanted, pool.map(lambda t: pm.locate(*t), wanted)):
            if span:
                found[zxy] = span
    groups = _merge(list(set(found.values())))
    total = sum(ln for _, ln in groups)
    progress(f"Downloading {len(found)} tiles ({total / 1e6:.1f} MB) in {len(groups)} requests...")
    blobs: dict[tuple[int, int], bytes] = {}

    def fetch(group: tuple[int, int]) -> tuple[tuple[int, int], bytes]:
        return group, pm.read(*group)
    done, shown = 0, 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (start, length), data in pool.map(fetch, groups):
            blobs[(start, length)] = data
            done += length
            if done * 10 // max(total, 1) > shown:
                shown = done * 10 // max(total, 1)
                progress(f"  {done / 1e6:.1f} of {total / 1e6:.1f} MB")
    starts = [g[0] for g in groups]

    def tile_bytes(span: tuple[int, int]) -> bytes:
        off, ln = span
        g = groups[bisect.bisect_right(starts, off) - 1]
        return blobs[g][off - g[0]:off - g[0] + ln]

    meta = pm.metadata()
    schema = str(meta.get("version", ""))
    if schema and schema.split(".")[0] != STYLE_SCHEMA:
        progress(f"Warning: these tiles use Protomaps schema {schema}, but the map style in web/vendor/map was made "
                 f"for schema {STYLE_SCHEMA}.x, so roads or labels may not show. Check the Nearby screen.")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE metadata (name TEXT, value TEXT);
        CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
    """)
    db.executemany("INSERT INTO tiles VALUES (?, ?, ?, ?)",
                   (((z, x, (1 << z) - 1 - y, tile_bytes(span)) for (z, x, y), span in found.items())))
    lon, lat = (detail[0] + detail[2]) / 2, (detail[1] + detail[3]) / 2
    info = {
        "name": "PTP offline map",
        "format": "pbf",
        "bounds": ",".join(f"{v:.5f}" for v in context),
        "center": f"{lon:.5f},{lat:.5f},14",
        "minzoom": str(pm.min_zoom),
        "maxzoom": str(detail_zoom),
        "attribution": meta.get("attribution", "© OpenStreetMap contributors"),
        "json": json.dumps({"vector_layers": meta.get("vector_layers", [])}),
        "ptp:compression": COMPRESSION[pm.tile_compression],
        "ptp:detail_bounds": ",".join(f"{v:.5f}" for v in detail),
        "ptp:detail_zoom": str(detail_zoom),
        "ptp:source": source,
        "ptp:schema": str(meta.get("version", "")),
        "ptp:retrieved": date.today().isoformat(),
    }
    db.executemany("INSERT INTO metadata VALUES (?, ?)", info.items())
    db.commit()
    db.close()
    os.replace(tmp, out)
    return {"tiles": len(found), "bytes": out.stat().st_size, "path": str(out)}


# ----------------------------------------------------------------------------- reading tiles


def _fields(buf: bytes):
    """(field number, wire type, value) of one protocol buffer message."""
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, pos = _varint(buf, pos)
        elif wire == 1:
            val, pos = buf[pos:pos + 8], pos + 8
        elif wire == 2:
            ln, pos = _varint(buf, pos)
            val, pos = buf[pos:pos + ln], pos + ln
        elif wire == 5:
            val, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield num, wire, val


def _packed(buf: bytes) -> list[int]:
    out, pos = [], 0
    while pos < len(buf):
        v, pos = _varint(buf, pos)
        out.append(v)
    return out


def _value(buf: bytes):
    for num, _, val in _fields(buf):
        if num == 1:
            return val.decode("utf-8", "replace")
        if num == 2:
            return struct.unpack("<f", val)[0]
        if num == 3:
            return struct.unpack("<d", val)[0]
        if num in (4, 5):
            return val
        if num == 6:
            return (val >> 1) ^ -(val & 1)
        if num == 7:
            return bool(val)
    return None


def _first_point(geometry: list[int]) -> tuple[int, int] | None:
    if len(geometry) >= 3 and geometry[0] & 7 == 1:  # MoveTo
        zz = lambda v: (v >> 1) ^ -(v & 1)
        return zz(geometry[1]), zz(geometry[2])
    return None


def features(tile: bytes, layer_name: str):
    """(properties, (x, y) in tile units 0..1) of the features of one layer of a vector tile.
    Only the first point of each feature: enough to place a pin."""
    for num, _, layer in _fields(tile):
        if num != 3:
            continue
        name, keys, values, feats, extent = None, [], [], [], 4096
        for n, _, v in _fields(layer):
            if n == 1:
                name = v.decode("utf-8")
            elif n == 2:
                feats.append(v)
            elif n == 3:
                keys.append(v.decode("utf-8"))
            elif n == 4:
                values.append(_value(v))
            elif n == 5:
                extent = v
        if name != layer_name:
            continue
        for f in feats:
            tags, geometry = [], []
            for n, _, v in _fields(f):
                if n == 2:
                    tags = _packed(v)
                elif n == 4:
                    geometry = _packed(v)
            point = _first_point(geometry)
            if point is None:
                continue
            props = {keys[tags[i]]: values[tags[i + 1]] for i in range(0, len(tags) - 1, 2)}
            yield props, (point[0] / extent, point[1] / extent)


# OpenStreetMap kinds and words in names -> the app's three kinds of facility.
MIDWIFE_WORDS = ("lying-in", "lying in", "lyingin", "birthing", "maternity", "paanakan", "midwife", "maternal")
CENTER_WORDS = ("health center", "health centre", "rural health", "rhu", "barangay health", "health station", "bhs")


def facility_kind(props: dict) -> str | None:
    kind = str(props.get("kind") or props.get("pmap:kind") or "")
    name = str(props.get("name") or "").casefold()
    if any(w in name for w in MIDWIFE_WORDS) or kind in ("birthing_centre", "birthing_center", "midwife"):
        return "midwife"
    if kind == "hospital":
        return "hospital"
    if kind in ("clinic", "doctors", "health_centre", "healthcare") or any(w in name for w in CENTER_WORDS):
        return "rhu"
    return None


def facilities_from_map(m: "MBTiles") -> list[dict]:
    """Named hospitals, clinics and birthing homes in the downloaded area, from its POI layer."""
    z = m.info["maxzoom"]
    found: list[dict] = []
    for x, y, data in m.tiles(z):
        tile = gzip.decompress(data) if m.info["compression"] == "gzip" else data
        for props, (fx, fy) in features(tile, "pois"):
            kind = facility_kind(props)
            name = str(props.get("name") or "").strip()
            if not kind or not name:
                continue
            lon, lat = tile_to_lonlat(z, x + fx, y + fy)
            if any(f["name"] == name and abs(f["lat"] - lat) < 0.003 and abs(f["lon"] - lon) < 0.003 for f in found):
                continue  # the same place, repeated at a tile edge
            found.append({"id": f"osm{len(found) + 1}", "name": name, "kind": kind,
                          "osm_kind": str(props.get("kind") or props.get("pmap:kind") or ""),
                          "lat": round(lat, 6), "lon": round(lon, 6)})
    found.sort(key=lambda f: (f["kind"], f["name"]))
    return found


# ----------------------------------------------------------------------------- serving


class MBTiles:
    """Reads the downloaded area. One SQLite connection per server thread."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self.info = self._read_info()

    def _db(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
            self._local.db = db
        return db

    def _read_info(self) -> dict:
        meta = dict(self._db().execute("SELECT name, value FROM metadata").fetchall())
        nums = lambda key: [float(v) for v in meta.get(key, "").split(",") if v]
        return {
            "bounds": nums("bounds"),
            "detail_bounds": nums("ptp:detail_bounds") or nums("bounds"),
            "center": nums("center"),
            "minzoom": int(meta.get("minzoom", 0)),
            "maxzoom": int(meta.get("maxzoom", 14)),
            "attribution": meta.get("attribution", "© OpenStreetMap contributors"),
            "compression": meta.get("ptp:compression", "gzip"),
            "retrieved": meta.get("ptp:retrieved", ""),
        }

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        row = self._db().execute(
            "SELECT tile_data FROM tiles WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?",
            (z, x, (1 << z) - 1 - y)).fetchone()
        return row[0] if row else None

    def tiles(self, z: int):
        """(x, y, data) of every tile at one zoom."""
        for x, row, data in self._db().execute(
                "SELECT tile_column, tile_row, tile_data FROM tiles WHERE zoom_level = ?", (z,)):
            yield x, (1 << z) - 1 - row, data


def load(path: Path | None = None) -> MBTiles | None:
    path = Path(path or map_path())
    if not path.is_file():
        return None
    try:
        return MBTiles(path)
    except sqlite3.Error as exc:
        sys.stderr.write(f"Offline map {path} could not be read: {exc}\n")
        return None


# ----------------------------------------------------------------------------- command line


def _default_point() -> tuple[float, float]:
    data = json.loads((ROOT / "data" / "facilities.json").read_text(encoding="utf-8"))
    loc = data["default_location"]
    return loc["lat"], loc["lon"]


def main(argv: list[str] | None = None) -> int:
    lat0, lon0 = _default_point()
    p = argparse.ArgumentParser(prog="python3 -m ptp.offline_map", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download the map of one area (needs internet, once)")
    d.add_argument("--lat", type=float, default=lat0, help=f"centre latitude (default {lat0}, from data/facilities.json)")
    d.add_argument("--lon", type=float, default=lon0, help=f"centre longitude (default {lon0})")
    d.add_argument("--km", type=float, default=8, help="street detail this far from the centre (default 8)")
    d.add_argument("--context-km", type=float, default=60, help="less detailed map this far (default 60)")
    d.add_argument("--source", help="a .pmtiles URL or file (default: newest build.protomaps.com build)")
    d.add_argument("--place", default="", help="name of the centre point, shown until the user sets a location")
    d.add_argument("--no-facilities", action="store_true",
                   help="keep the current facility list instead of the hospitals and clinics found in the map")
    sub.add_parser("info", help="show what map is downloaded")
    args = p.parse_args(argv)

    if args.cmd == "info":
        m = load()
        if not m:
            print(f"No offline map at {map_path()}. Run: python3 -m ptp.offline_map download --lat ... --lon ...")
            return 1
        print(json.dumps(m.info, indent=2))
        return 0

    if args.km > 25 or args.context_km > 200:
        p.error("keep --km at 25 or less and --context-km at 200 or less: bigger areas take hundreds of MB")
    t0 = time.time()
    out = map_path()
    try:
        source = args.source or latest_build()
        print(f"Map source: {source}")
        report = extract(open_source(source), out, area_bbox(args.lat, args.lon, args.km),
                         area_bbox(args.lat, args.lon, args.context_km), source=source)
    except (OSError, ValueError) as exc:
        print(f"Map download failed: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {report['tiles']} tiles, {report['bytes'] / 1e6:.1f} MB, to {report['path']} "
          f"in {time.time() - t0:.0f} s. The Nearby screen now shows this map, also offline.")
    if not args.no_facilities:
        found = facilities_from_map(MBTiles(out))
        if found:
            facilities_path().write_text(json.dumps({
                "sample": False,
                "source": "openstreetmap",
                "retrieved": date.today().isoformat(),
                "note": "Hospitals, clinics and birthing homes from OpenStreetMap (ODbL), found in the downloaded "
                        "map. Not checked by PTP: names and places can be wrong or out of date.",
                "default_location": {"name": args.place, "lat": args.lat, "lon": args.lon},
                "facilities": found,
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            counts = {k: sum(f["kind"] == k for f in found) for k in ("hospital", "midwife", "rhu")}
            print(f"Found {len(found)} facilities on the map ({counts['hospital']} hospitals, {counts['midwife']} "
                  f"birthing or lying-in, {counts['rhu']} health centers or clinics), saved to {facilities_path()}. "
                  f"Check them: OpenStreetMap can be incomplete. To use your own list instead, delete that file.")
        else:
            print("No named hospitals or clinics found on the map; Nearby keeps data/facilities.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
