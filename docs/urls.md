# Shareable links

Everything that shapes what you see in RINF Atlas is in the address bar, in
the part after `#`. Copying the URL (or using **Share** in the top bar)
gives a link that opens the same view for someone else: environment, map
position, classes, filters, search text, datasets, selected feature and its
tab, map colours, data issues, classes drawn at every zoom, line profile,
vehicle profile and map layers.

The code is `web/src/url.ts`. This page is the contract: links that work
today keep working.

## Example

```
#env=prod&map=9.5/50.84/4.36&c=section-of-line&f=country:cty:BEL;section-of-line/lengthOfSectionOfLine:1000..
&q=Brussel&sel=era:functionalInfrastructure/sectionsOfLine/SOL_BE1&tab=describe
&col=running-track/maximumPermittedSpeed&colh=bin:0&qa=1&lp=line,50A
&veh=wheelSetGauge:erac:nominal-track-gauges/30;trainLength:200&vehn=My+EMU
```

(One line in practice; broken here for reading.)

## Parameters

Parameters are `key=value` pairs joined with `&`, in the order below. A
parameter that is absent has its default. When a link is opened, or on
Back / Forward, every parameter that is absent is reset to its default.

| Key | Value | Default |
|---|---|---|
| `env` | Environment key (`prod`, `uat`, ...) | the server's default environment |
| `map` | `zoom/lat/lng`, at most 2 / 5 / 5 decimals, trailing zeros dropped | the initial view |
| `c` | Selected classes, `,` separated (`section-of-line,tunnel`) | none |
| `f` | Facet filters, see [Filters](#filters) | none |
| `q` | Search text | empty |
| `ds` | Datasets (provider graph IRIs), `,` separated [items](#items) | all |
| `v` | `1` = limit results to the map view | off |
| `sel` | Selected feature IRI, an [item](#items) | none |
| `tab` | Detail tab of the selected feature: `describe` or `graph` | `properties` |
| `nav` | Describe tab: the IRIs followed from the selected feature, `,` separated items | none |
| `dir` | Describe tab: `in` = incoming triples | `out` |
| `side` | Side panel tab: `results` | `filters` |
| `col` | Map colouring: facet key, plain or `class/key` | off |
| `colh` | Legend entries hidden, `,` separated items (`bin:0`, `__nodata__`, values) | none |
| `qa` | `1` = show data issues on the map | off |
| `az` | Classes drawn at every zoom, ignoring their minimum zoom, `,` separated | none |
| `lp` | Line profile open; its subject, see [Line profile](#line-profile). Present and empty = open with nothing chosen | closed |
| `lpr` | Line profile rows (facet keys), `,` separated; `-` = no rows | the user's own rows |
| `veh` | Vehicle compatibility on the map with this vehicle, see [Vehicle](#vehicle) | off |
| `vehn` | Name of that vehicle | `Shared vehicle` |
| `vp` | `1` = the Vehicle check panel is open | closed |
| `orm` | `1` = OpenRailwayMap overlay | off |
| `lyr` | ids of the instance's extra map layers that are on, `,` separated ([layers.md](layers.md)); unknown ids are dropped | none |

`tab`, `nav` and `dir` are read only with `sel`; `colh` only with `col`;
`lpr` only with `lp`; `vehn` only with `veh`.

## Encoding

Values are percent-encoded, except `/ : , ; @`, which are safe after `#` and
are left as they are to keep links readable. A space is written `+`.

### Items

Lists use `,` between items. Filters and the vehicle use `;` between
entries. Inside an item, `%`, `,` and `;` are written `%25`, `%2C` and `%3B`.
In `colh` a space is written `%20`, so older space-separated links can still
be told apart.

IRIs in these namespaces are shortened to `prefix:local`:

| Prefix | Namespace |
|---|---|
| `erac` | `http://data.europa.eu/949/concepts/` |
| `era` | `http://data.europa.eu/949/` |
| `cty` | `http://publications.europa.eu/resource/authority/country/` |
| `eu` | `http://publications.europa.eu/resource/authority/` |
| `sh` | `http://www.w3.org/ns/shacl#` |
| `atlas` | `https://rinf-atlas.matdata.eu/ns#` |

The longest matching namespace wins. A mapping is never changed or removed,
because old links depend on it. A new one can be added. Full IRIs are always
accepted too.

Values in filters and the vehicle are typed:
- `!t` and `!f` are the booleans true and false.
- A bare number (`1435`, `-2.5`, `1e3`) is a number.
- Anything else is a string.

A string that would read as something else gets a leading `'`. That covers
the empty string, a string starting with `'` or `!`, a number-like string,
a string containing `..`, and a string that looks like a known prefix.
Examples: `'50` is the text "50", and `'era:x` is the literal text "era:x".

### Filters

`f` holds `key:value,value;key:min..max`:
- The key is the facet key (`country`, `operational-point/opType`). A `:`
  inside a key is written `%3A`.
- Terms and boolean facets list their items.
- Range facets give `min..max`, either side may be empty (`1000..`, `..80`).

### Line profile

`lp` is one of:
- `line,<line id>[,<dataset IRI>]`: a national line, optionally from one dataset.
- `sec,<section IRI>,<section IRI>,...`: a list of sections of line.
- `route,<from operational point IRI>,<to IRI>`: a route.

The mode gets `-r` when the sequence is reversed (`line-r,50A`).

### Vehicle

`veh` holds the filled-in characteristics of the vehicle profile shown on the
map, in the form `key:value,value;key:number`. The keys are the
compatibility rules' keys (see `/api/compatibility/rules`).

Saved vehicle profiles stay in the user's browser and are never put in a
link. When the link is opened:
- If the recipient has a saved profile with the same name and values, it is
  used.
- Otherwise the vehicle is shown as "Shared: name (not saved)", with a
  **Save to my profiles** button.

### Self-contained links

A link carries everything it needs: the vehicle's values and the line
profile's rows travel in the link, not only a reference to the sender's
browser storage. What a link leaves out comes from the recipient's own
settings: theme, saved profiles, and default profile rows when `lpr` is
absent.

## History

- **Push.** A new history entry is added for navigation, so Back undoes it.
  Navigation is a change of environment, classes, filters, datasets, search
  text, selection, detail tab, Describe step or direction, map colouring,
  data issues, classes drawn at every zoom, opening or changing the line
  profile, opening or closing
  the Vehicle check panel, or switching vehicle.
  Several such changes within 1.5 s share one entry, for example ticking a
  few values.
- **Replace.** Continuous changes replace the current entry. These are pan
  and zoom, legend toggles, profile rows, vehicle values, and the side panel
  tab.
- Back / Forward and editing the hash by hand apply the URL without adding
  an entry.

## Older links

Links from before this scheme are read as before and rewritten in the new
form once the page has opened them:

| Old form | Now |
|---|---|
| `f` as JSON (`{"country":["http://..."]}`) | `f=country:cty:BEL` |
| `lp` as JSON (`{"mode":"line","line":"50A"}`) | `lp=line,50A` |
| `ds` and `colh` separated by spaces | `,` separated |
| full IRIs in `sel`, `ds`, `f` | `prefix:local` |
| `map` with fixed decimals (`8.00/50.85000/4.35000`) | `8/50.85/4.35` |
| `side=vehicle` (the vehicle check was a side tab) | `vp=1` |

## Keeping links short

A typical link with a selected feature, colours, a line profile and a vehicle
is 600 to 700 characters. Most of that is the vehicle's values. Without the
vehicle, the same link is about 350 characters.
