"""The offline street map: reading a PMTiles archive, saving one area, serving it, and finding
facilities in it. The archives here are built by the tests, so no download is needed."""
import gzip
import json
import shutil
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from ptp import offline_map as om
from ptp import server

# ----------------------------------------------------------------------------- tiny encoders


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def directory(entries):
    """PMTiles directory bytes for (tile_id, offset, length, run_length) entries."""
    out = varint(len(entries))
    last = 0
    for tid, *_ in entries:
        out += varint(tid - last)
        last = tid
    for e in entries:
        out += varint(e[3])
    for e in entries:
        out += varint(e[2])
    for i, e in enumerate(entries):
        follows = i > 0 and e[1] == entries[i - 1][1] + entries[i - 1][2]
        out += varint(0 if follows else e[1] + 1)
    return gzip.compress(out)


def pmtiles(tiles: dict, leaf: bool = False, max_zoom: int = 15) -> bytes:
    """A PMTiles v3 archive of {(z, x, y): bytes}. Tiles are gzip-compressed like Protomaps
    builds. With leaf=True the tile entries sit in a leaf directory, as in large archives."""
    data, entries, seen = b"", [], {}
    for (z, x, y), raw in sorted(tiles.items(), key=lambda t: om.tile_id(*t[0])):
        blob = gzip.compress(raw, mtime=0)
        if blob in seen:  # deduplicated, like ocean tiles in real builds
            off = seen[blob]
        else:
            off = seen[blob] = len(data)
            data += blob
        entries.append((om.tile_id(z, x, y), off, len(blob), 1))
    meta = gzip.compress(json.dumps({"attribution": "© OpenStreetMap contributors", "version": "4.0.0",
                                     "vector_layers": [{"id": "pois"}]}).encode())
    leaves = b""
    if leaf:
        leaves = directory(entries)
        root = directory([(entries[0][0], 0, len(leaves), 0)])
    else:
        root = directory(entries)
    root_off = 127
    meta_off = root_off + len(root)
    leaf_off = meta_off + len(meta)
    data_off = leaf_off + len(leaves)
    header = b"PMTiles" + bytes([3]) + struct.pack(
        "<11Q", root_off, len(root), meta_off, len(meta), leaf_off, len(leaves), data_off, len(data),
        len(entries), len(entries), len(seen))
    header += struct.pack("<6B", 1, 2, 2, 1, 0, max_zoom) + struct.pack("<4i", -1800000000, -850000000, 1800000000, 850000000)
    header += struct.pack("<Bii", 0, 0, 0)
    assert len(header) == 127
    return header + root + meta + leaves + data


def message(*fields) -> bytes:
    """Protocol buffer message from (number, value) pairs: int -> varint, bytes/str -> length-delimited."""
    out = b""
    for num, val in fields:
        if isinstance(val, int):
            out += varint(num << 3) + varint(val)
        else:
            val = val.encode() if isinstance(val, str) else val
            out += varint(num << 3 | 2) + varint(len(val)) + val
    return out


def poi_tile(points) -> bytes:
    """A vector tile with a "pois" layer of points: [(properties, x, y)] with x, y in 0..4096."""
    keys, values, feats = [], [], []
    for props, x, y in points:
        tags = b""
        for k, v in props.items():
            if k not in keys:
                keys.append(k)
            if v not in values:
                values.append(v)
            tags += varint(keys.index(k)) + varint(values.index(v))
        geom = varint(1 | 1 << 3) + varint((x << 1) ^ (x >> 31)) + varint((y << 1) ^ (y >> 31))
        feats.append(message((3, 1), (2, tags), (4, geom)))
    layer = message((15, 2), (1, "pois"), *[(2, f) for f in feats], *[(3, k) for k in keys],
                    *[(4, message((1, v))) for v in values], (5, 4096))
    return message((3, layer))


# ----------------------------------------------------------------------------- tests


class TileMaths(unittest.TestCase):
    def test_tile_ids_match_the_pmtiles_spec(self):
        # Values from the official PMTiles library (zxyToTileId).
        for (z, x, y), tid in {(0, 0, 0): 0, (1, 0, 0): 1, (1, 0, 1): 2, (1, 1, 1): 3, (1, 1, 0): 4, (2, 0, 0): 5,
                               (12, 3423, 1763): 19078479, (15, 27360, 15138): 1222593891}.items():
            self.assertEqual(om.tile_id(z, x, y), tid, (z, x, y))

    def test_area_covers_the_point(self):
        bbox = om.area_bbox(14.85, 120.81, 8)
        tiles = om.tiles_in_bbox(bbox, 15)
        self.assertIn((15, *om.lonlat_to_tile(120.81, 14.85, 15)), tiles)
        self.assertLess(len(tiles), 300)  # about 16 km square at street detail: a few MB, not hundreds


class ReadAndExtract(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        # Street detail around Malolos at zoom 15, plus every tile above it.
        z15 = om.lonlat_to_tile(120.81, 14.85, 15)
        self.tiles = {(15, *z15): b"street tile", (15, z15[0] + 1, z15[1]): b"next tile"}
        for z in range(15):
            self.tiles[(z, z15[0] >> (15 - z), z15[1] >> (15 - z))] = b"ocean" if z < 5 else f"zoom {z}".encode()

    def archive(self, leaf):
        path = self.dir / f"area{int(leaf)}.pmtiles"
        path.write_bytes(pmtiles(self.tiles, leaf=leaf))
        return om.open_source(str(path))

    def test_reads_every_tile_with_and_without_leaf_directories(self):
        for leaf in (False, True):
            pm = self.archive(leaf)
            for zxy, raw in self.tiles.items():
                span = pm.locate(*zxy)
                self.assertIsNotNone(span, zxy)
                self.assertEqual(gzip.decompress(pm.read(*span)), raw)
            self.assertIsNone(pm.locate(15, 0, 0))
            self.assertEqual(pm.metadata()["version"], "4.0.0")

    def test_extract_saves_an_area_that_the_server_reads_back(self):
        pm = self.archive(True)
        out = self.dir / "map" / "area.mbtiles"
        report = om.extract(pm, out, om.area_bbox(14.85, 120.81, 2), om.area_bbox(14.85, 120.81, 30),
                            progress=lambda msg: None)
        self.assertEqual(report["tiles"], len(self.tiles))
        m = om.MBTiles(out)
        for zxy, raw in self.tiles.items():
            self.assertEqual(gzip.decompress(m.tile(*zxy)), raw, zxy)
        self.assertIsNone(m.tile(15, 0, 0))
        self.assertEqual(m.info["maxzoom"], 15)
        self.assertEqual(m.info["compression"], "gzip")
        self.assertIn("OpenStreetMap", m.info["attribution"])
        self.assertFalse(out.with_suffix(".part").exists())

    def test_a_remote_server_that_ignores_ranges_is_refused(self):
        # It would send the whole planet (over 100 GB) instead of a few tiles.
        class Whole(server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "7")
                self.end_headers()
                self.wfile.write(b"PMTiles")

            def log_message(self, *a):
                pass
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), Whole)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        read = om.http_reader(f"http://127.0.0.1:{httpd.server_port}/planet.pmtiles", timeout=5)
        with self.assertRaises(ValueError):
            read(0, 7)


class Facilities(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(om.facility_kind({"kind": "hospital", "name": "Bulacan Medical Center"}), "hospital")
        self.assertEqual(om.facility_kind({"kind": "clinic", "name": "Santos Lying-In Clinic"}), "midwife")
        self.assertEqual(om.facility_kind({"kind": "clinic", "name": "Dr. Cruz Clinic"}), "rhu")
        self.assertEqual(om.facility_kind({"kind": "townhall", "name": "Barangay Health Station"}), "rhu")
        self.assertEqual(om.facility_kind({"pmap:kind": "hospital", "name": "Old schema"}), "hospital")
        self.assertIsNone(om.facility_kind({"kind": "pharmacy", "name": "Mercury Drug"}))
        self.assertIsNone(om.facility_kind({"kind": "restaurant", "name": "Jollibee"}))

    def test_facilities_are_found_in_the_map_tiles(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        x, y = om.lonlat_to_tile(120.81, 14.85, 15)
        tile = poi_tile([({"kind": "hospital", "name": "Bulacan Medical Center"}, 2048, 2048),
                         ({"kind": "clinic", "name": "Santos Lying-In Clinic"}, 100, 4000),
                         ({"kind": "pharmacy", "name": "Mercury Drug"}, 10, 10),
                         ({"kind": "hospital"}, 30, 30)])  # no name: cannot be listed
        (tmp / "a.pmtiles").write_bytes(pmtiles({(15, x, y): tile, (0, 0, 0): b""}))
        out = tmp / "area.mbtiles"
        om.extract(om.open_source(str(tmp / "a.pmtiles")), out, om.area_bbox(14.85, 120.81, 1),
                   om.area_bbox(14.85, 120.81, 1), progress=lambda msg: None)
        found = om.facilities_from_map(om.MBTiles(out))
        self.assertEqual([(f["name"], f["kind"]) for f in found],
                         [("Bulacan Medical Center", "hospital"), ("Santos Lying-In Clinic", "midwife")])
        lon, lat = om.tile_to_lonlat(15, x + 0.5, y + 0.5)
        self.assertAlmostEqual(found[0]["lat"], lat, places=5)
        self.assertAlmostEqual(found[0]["lon"], lon, places=5)


class Serving(unittest.TestCase):
    """Tiles, fonts and map info over HTTP, as the Nearby screen asks for them."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        tiles = {(14, 13690, 7570): b"one tile", (0, 0, 0): b"world"}
        (cls.tmp / "a.pmtiles").write_bytes(pmtiles(tiles, max_zoom=14))
        cls.map = cls.tmp / "area.mbtiles"
        lon, lat = om.tile_to_lonlat(14, 13690.5, 7570.5)
        area = om.area_bbox(lat, lon, 0.5)
        om.extract(om.open_source(str(cls.tmp / "a.pmtiles")), cls.map, area, area, detail_zoom=14,
                   progress=lambda msg: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def start(self, map_path):
        from ptp.llm import MockLLM
        from ptp.store import Store
        app = server.App(store=Store(self.tmp / f"state{id(map_path)}"), llm=MockLLM(), map_path=map_path)
        httpd = server.serve(app, "127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return app, f"http://127.0.0.1:{httpd.server_port}"

    def get(self, url):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_tiles_are_served_gzipped_and_missing_ones_are_empty(self):
        app, base = self.start(self.map)
        status, headers, body = self.get(base + "/map/tiles/14/13690/7570.mvt")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(body), b"one tile")
        self.assertEqual(self.get(base + "/map/tiles/14/1/1.mvt")[0], 204)
        info = json.loads(self.get(base + "/api/map")[2])
        self.assertTrue(info["available"])
        self.assertEqual(info["maxzoom"], 14)
        self.assertTrue(app.health()["offline_map"])

    def test_without_a_map_nearby_falls_back(self):
        app, base = self.start(self.tmp / "nothing.mbtiles")
        self.assertEqual(json.loads(self.get(base + "/api/map")[2]), {"available": False})
        self.assertEqual(self.get(base + "/map/tiles/14/13690/7570.mvt")[0], 204)
        self.assertFalse(app.health()["offline_map"])

    def test_map_files_are_bundled_and_served(self):
        _, base = self.start(self.map)
        for path in ("/vendor/map/maplibre-gl.js", "/vendor/map/maplibre-gl.css", "/vendor/map/style.json",
                     "/vendor/map/sprites/light.json", "/vendor/map/sprites/light@2x.png",
                     "/vendor/map/fonts/Noto%20Sans%20Regular/0-255.pbf"):
            status, _, body = self.get(base + path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body, path)
        # Letters outside the bundled ranges get an empty answer, not an error.
        status, _, body = self.get(base + "/vendor/map/fonts/Noto%20Sans%20Regular/19968-20223.pbf")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(self.get(base + "/vendor/map/fonts/..%2F..%2F..%2F..%2Fptp%2Fserver.py")[0], 404)

    def test_style_uses_only_files_on_this_device(self):
        style = json.loads((server.WEB / "vendor" / "map" / "style.json").read_text(encoding="utf-8"))
        urls = [style["glyphs"], style["sprite"], *style["sources"]["protomaps"]["tiles"]]
        self.assertTrue(all(u.startswith("/") and "//" not in u for u in urls), urls)
        fonts = set()
        json.dumps(style["layers"], default=str)
        for layer in style["layers"]:
            font = layer.get("layout", {}).get("text-font")
            if isinstance(font, list) and all(isinstance(f, str) for f in font):
                fonts.update(font)
        for f in fonts:
            self.assertTrue((server.WEB / "vendor" / "map" / "fonts" / f / "0-255.pbf").is_file(), f)


if __name__ == "__main__":
    unittest.main()
