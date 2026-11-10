# ERATV vehicle types

RINF Atlas can load the vehicle types of ERATV, the European register of authorised
types of vehicles (<https://eratv.era.europa.eu/>), and convert them to RDF with the
ERA ontology, so the vehicle check can start from an authorised type instead of
characteristics typed by hand.

## In the vehicle check

The vehicle check panel has a **From ERATV** block. Enter a Type ID
(`11-256-0001-3-002-001`) or paste the link to the type's ERATV page and choose
**Load**. RINF Atlas fetches that one type, converts it and shows its name, category,
how much of it converted, its configurations and its authorisations. **Add** turns
each chosen configuration into a vehicle profile you can check and edit:

- A configuration ("1435mm / AC 15kV-16.7Hz / PZB 90") takes its own gauge, energy
  supply system and class B system, the values ERATV gives for it or for one of its
  parts ("AC 15kV-16.7Hz"), and the values of the whole type.
- The maximum speed is the maximum design speed (4.1.2.1) when ERATV gives no speed per
  cant deficiency.
- Authorisations, areas of use and restrictions are shown for information only. They
  are not part of the check.
- The profile's note names the Type ID, the configuration, the date and the link.

How a lookup works:

- The API never contacts ERATV. It queues the lookup (`eratv_request`) and the worker
  does it: the type's page, then its XML export, in a new session (two requests). A
  type already stored answers at once; **Fetch again** fetches it anew (at most once an
  hour).
- At most `ATLAS_ERATV_LOOKUPS_PER_HOUR` (30) fetches an hour across all users.
- If ERATV shows its CAPTCHA, the lookup stops (RINF Atlas never answers it) and
  lookups pause for an hour. The panel then links to the type's ERATV page: download
  the XML export there and choose **Upload XML…**. An upload needs no request to
  ERATV, and works with `ATLAS_ERATV_LOOKUP=0` too. Drafts and cancelled records are
  refused.

API: `POST /api/eratv/types/{Type ID}/fetch[?refresh=true]`, `POST
/api/eratv/upload?filename=` (the XML as the body), `GET /api/eratv/requests/{id}`,
`GET /api/eratv/types/{Type ID}` (summary, Turtle, values not converted) and `GET
/api/eratv/types?q=` (stored types).

## Getting the data

ERATV has no API, SPARQL endpoint or bulk download. Its web application exports one
type at a time as XML. There are two ways to get those files into Atlas.

### Import XML files (no request to ERATV)

```sh
docker compose exec worker atlas eratv import /data/eratv-xml/            # a folder
docker compose exec worker atlas eratv import VehicleType_11-256-0001-3-002-001_PUBLISHED.xml
```

This works with files downloaded by hand from a type's page in ERATV ("XML") or a
bulk export ERA provides. Files whose name ends in `_DRAFT.xml` or `_CANCELLED.xml`
are skipped.

### Harvest everything (only with ERA's agreement)

```sh
docker compose exec worker atlas eratv fetch 11-256-0001-3-002-001   # these Type IDs now
docker compose exec worker atlas eratv sync                          # what changed on the newest list page
docker compose exec worker atlas eratv full [--from N] [--to N]      # every internal id; resumable
```

With `ATLAS_ERATV=1` the worker does this by itself in a background thread. It runs a
full pass first and then every `ATLAS_ERATV_FULL_DAYS` (30). In between, every
`ATLAS_ERATV_SYNC_HOURS` (6) it checks the newest page of ERATV's list (sorted by
"Last Update") and fetches the types that changed. It is **off by default**: it
reads another organisation's web application, and ERATV's `robots.txt` disallows
crawlers, so turn it on only once ERA has agreed.

How the harvester behaves:

- One request every `ATLAS_ERATV_DELAY` seconds (2). The User-Agent names RINF
  Atlas, plus `ATLAS_ERATV_CONTACT` if you set one.
- Each type takes two requests: the type's page, then its XML export (ERATV only
  sends the file after the page has been opened in the same session). The page
  shows whether the record is public. Drafts and cancelled records are never
  downloaded.
- On server errors or the firewall's "Request Rejected" page, it backs off and then
  stops the run.
- **ERATV shows a CAPTCHA after a few pages.** The harvester never tries to get past
  it. The run stops and the worker tries again a day later, and a full pass resumes
  where it stopped. Harvesting at scale therefore needs ERA to exempt this client or
  to provide an export. Until then, use `import`.
- Contact data of the manufacturer and the authorisation holder (address, e-mail,
  phone) is dropped while reading. Only organisation names are kept.

`atlas eratv status` shows what is stored and the last runs.

## Storage

| Table | |
|---|---|
| `eratv_type` | one row per published Type ID: the XML as exported, the Turtle, a JSON summary (configurations, authorisations, restrictions), the values that did not map, and coverage |
| `eratv_id` | what each internal id answered (published, not public, nothing), so a full pass can resume |
| `eratv_run` | harvest runs: requests, fetched, changed, failed, where a full pass resumes |
| `eratv_vocab` | the mapping below, copied from an environment's mirror |

`atlas eratv reconvert` converts the stored XML again, for example after ERA
publishes a new ontology. It sends no request to ERATV.

## Conversion to RDF

The mapping comes from the ERA ontology itself. About 115 properties carry
`era:eratvIndex`, the ERATV parameter number, together with their range, unit and
SKOS concept scheme. The values are looked up in ERA's SKOS lists by label. Both the
ontology and the lists are already in every environment's mirror (the vocabulary
graphs), so the conversion needs no extra download. A concept that RINF also uses
wins over an ERATV-only concept with the same label, so a vehicle and a track
compare on the same IRI.

The model and IRIs follow ERA's example
(`era-kg/examples/evr/era-evr-eratv-sample.ttl` in the era-ontology repository):

```
eratv:{Type ID}  a era:VehicleType                      (http://data.europa.eu/949/vehicleTypes/…)
  era:vehicleTypeNumber "11-256-0001-3" ; era:includedVersionsVariant "002-001" ;
  era:previousVehicleType eratv:11-256-0001-3-001-001 ;
  era:maximumDesignSpeed 80 ; era:wheelSetGauge … ; era:protectionLegacySystem … ;
  era:configDependentParameter era-vtcps:cps-{Type ID}-{configuration} .
era-vtcps:…  a era:VehicleTypeConfigParameterSet ;
  era:hasVehicleTypeConfiguration era-vtconfig:cfg-{configuration} ;
  era:minimumContactWireHeight 4.6 ; era:maximumContactWireHeight 6.2 ; …
era-vtconfig:…  a era:VehicleTypeConfiguration ;
  era:forWheelSetGauge … ; era:forEnergySupplySystem … ; era:forOnBoardClassB … .
era-vtap:…  a era:VehicleTypeAuthorisationApplication ;
  vpa:constitutedBy era-vtacs:… ;            # era:VehicleTypeAuthorisationCase
  vpa:requestFor   era-vta:… .               # era:VehicleTypeAuthorisation
```

- There is one `era:VehicleType` per published Type ID, so each version and variant
  is its own type.
- A value that ERATV gives for one configuration ("1435mm / AC 15kV-16.7Hz / ATC v2")
  or one energy supply system ("AC 15kV-16.7Hz") goes to that configuration's
  parameter set. Every other value goes to the vehicle type.
- Authorisations: `era:areaOfUse` and `era:authorisingMemberState` (left out for an
  EU authorisation that lists several countries), plus `era:nonCodedRestrictions`.
  The authorisation status, validity, holder name, authorisation document reference
  (EIN) and coded restrictions use `atlas:` properties. Coded restrictions are
  `era:VehicleTypeAuthorisationRestriction` nodes with `atlas:restrictionCode` and
  `atlas:restrictionValue`, and for 1.1 also `atlas:minimumCurveRadius`, the highest
  figure when several are given ("100 m in depot / 150 m for revenue service" gives
  150).
- A value that maps to no property, or to no concept with that label, is **not
  guessed**. It is listed in `eratv_type.unmapped` with the reason, and counted in
  `coverage`. Examples are the permissible payload per line category, which is not
  converted yet, and pantograph heads such as "1950 sc DE and AT".

`atlas eratv convert FILE.xml [--vocab ontology+skos.ttl] [--json]` converts one
file without a database, for a quick look.
