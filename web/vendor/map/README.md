# Offline map files

Everything the Nearby street map needs besides the map data itself, bundled so the map works with
the internet off. The map data (OpenStreetMap vector tiles for one area) is downloaded once with
`python3 -m ptp.offline_map download` and served by PTP from `data/map/area.mbtiles`.

| File | What | Version | License |
|---|---|---|---|
| `maplibre-gl.js`, `maplibre-gl.css` | MapLibre GL JS, draws the map | 5.6.0 (npm `maplibre-gl`) | BSD-3-Clause, `LICENSE-maplibre.txt` |
| `style.json` | Map colours and layers ("light"), made with `layers("protomaps", namedFlavor("light"), {lang: "en"})` from npm `@protomaps/basemaps`, with PTP's own tile, font and icon paths | 5.7.2, for Protomaps tiles schema 4 | BSD-3-Clause |
| `fonts/Noto Sans */*.pbf` | Noto Sans Regular, Medium and Italic, letters U+0000-U+01FF and U+2000-U+20FF (Latin, including ñ, and punctuation), from github.com/protomaps/basemaps-assets | main branch, 9 Oct 2026 | SIL Open Font License 1.1, `fonts/OFL.txt` |
| `sprites/light*` | Map icons, from github.com/protomaps/basemaps-assets `sprites/v4` | main branch, 9 Oct 2026 | Derived from tangrams/icons, MIT, `sprites/LICENSE-tangrams-icons.md` |

Letters outside the bundled font ranges are skipped on the map (the server answers those requests
with an empty font file). Map data: © OpenStreetMap contributors, ODbL 1.0; the credit is shown on
the map.
