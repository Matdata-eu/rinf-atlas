"""One ERATV vehicle type (parsed XML) to RDF with the ERA ontology.

The model and the IRI patterns follow ERA's own example
(era-ontology: era-kg/examples/evr/era-evr-eratv-sample.ttl):

  era:VehicleType                      <949/vehicleTypes/{Type ID}>
    era:configDependentParameter  ->   era:VehicleTypeConfigParameterSet
                                       <949/vehicleTypes/configParameterSets/cps-{Type ID}-{configuration}>
      era:hasVehicleTypeConfiguration -> era:VehicleTypeConfiguration
                                       <949/vehicleTypes/configurations/cfg-{configuration}>
  era:VehicleTypeAuthorisationApplication  <949/vehicleTypes/authorisations/applications/vtapp-...>
    vpa:constitutedBy -> era:VehicleTypeAuthorisationCase <.../authorisations/cases/vtac-...>
                           vpa:concerns -> the vehicle type
    vpa:requestFor    -> era:VehicleTypeAuthorisation     <.../authorisations/auth-...>

One era:VehicleType per published Type ID, i.e. per version and variant
(11-256-0001-3-002-001 is type 11-256-0001-3, version 002, variant 001); the previous
one is linked with era:previousVehicleType.

Which property a parameter goes to comes from `era:eratvIndex` in the ontology
(vocab.py). Values given for one configuration (or one energy supply system) go to
that configuration's parameter set, the others to the vehicle type. Values that do
not map to the ontology (no property, or no concept with that label) are kept in
`Result.unmapped` so the gaps can be counted; nothing is guessed.

Facts the ontology has no property for (authorisation status, holder name, coded
restrictions, ...) use the atlas: namespace.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

import pyoxigraph as ox

from .parse import Param, Record
from .vocab import (
    CATEGORY_SCHEME,
    CONFIG_SCHEME,
    ENERGY_SCHEME,
    ERA,
    GAUGE_SCHEME,
    SUBCATEGORY_SCHEME,
    TPLS_SCHEME,
    XSD,
    Prop,
    Vocab,
)

ATLAS = "https://rinf-atlas.matdata.eu/ns#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
DCT = "http://purl.org/dc/terms/"
VPA = "https://w3id.org/vpa#"
COUNTRY = "http://publications.europa.eu/resource/authority/country/"
REGMETHOD_SCHEME = ERA + "concepts/vehicle-type-registration-methods/VehicleTypeRegistrationMethods"
VT = ERA + "vehicleTypes/"
VIEW_URL = "https://eratv.era.europa.eu/Eratv/Home/View/"
SOURCE = "https://eratv.era.europa.eu/"

PREFIXES = {"era": ERA, "atlas": ATLAS, "rdf": RDF, "rdfs": RDFS, "dcterms": DCT, "vpa": VPA, "xsd": XSD,
            "country": COUNTRY, "eratv": VT, "era-vtcps": VT + "configParameterSets/",
            "era-vtconfig": VT + "configurations/", "era-vta": VT + "authorisations/",
            "era-vtacs": VT + "authorisations/cases/", "era-vtap": VT + "authorisations/applications/",
            "era-vtres": VT + "authorisations/restrictions/"}

# ISO 3166 alpha-2 (as ERATV writes it, EL for Greece) -> the EU country authority code
COUNTRIES = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "HR": "HRV", "CY": "CYP", "CZ": "CZE", "DK": "DNK", "EE": "EST",
    "FI": "FIN", "FR": "FRA", "DE": "DEU", "EL": "GRC", "GR": "GRC", "HU": "HUN", "IE": "IRL", "IT": "ITA",
    "LV": "LVA", "LT": "LTU", "LU": "LUX", "MT": "MLT", "NL": "NLD", "PL": "POL", "PT": "PRT", "RO": "ROU",
    "SK": "SVK", "SI": "SVN", "ES": "ESP", "SE": "SWE", "NO": "NOR", "CH": "CHE", "UK": "GBR", "GB": "GBR",
    "EU": "EUR", "LI": "LIE", "ME": "MNE", "MK": "MKD", "RS": "SRB", "TR": "TUR", "UA": "UKR", "MD": "MDA",
}

# ERATV unit text -> QUDT unit (the ontology's era:unitOfMeasure)
_UNITS = {
    "mm": "MilliM", "m": "M", "km": "KiloM", "km/h": "KiloM-PER-HR", "kg": "KiloGM", "t": "TONNE",
    "tonnes": "TONNE", "tonne": "TONNE", "m/s2": "M-PER-SEC2", "m/s²": "M-PER-SEC2", "n": "N", "kn": "KiloN",
    "a": "A", "kw": "KiloW", "kj": "KiloJ", "‰ (mm/m)": "MilliM-PER-M", "‰": "MilliM-PER-M",
    "mm/m": "MilliM-PER-M", "db(a)": "DeciB_A", "db": "DeciB_A", "min": "MIN", "g/s": "GM-PER-SEC",
    "ohm": "OHM", "ω": "OHM",
}
# QUDT unit -> (dimension, factor to the dimension's base unit)
_SCALE = {
    "MilliM": ("L", 0.001), "M": ("L", 1.0), "KiloM": ("L", 1000.0),
    "KiloGM": ("M", 1.0), "TONNE": ("M", 1000.0), "N": ("F", 1.0), "KiloN": ("F", 1000.0),
}

# Parameters with their own handler (several properties, structured text, ...)
_SPECIAL = {"0.1", "0.2", "0.3", "0.4", "1.1", "1.2", "1.4", "1.5", "4.10.5", "4.10.8", "4.5.3.4", "4.6.4",
            "4.7.5", "4.7.6", "4.7.7", "4.13.1.5", "4.13.2.3", "4.5.1"}
# Where an index has several current properties, the one a plain value goes to
_PREFER = {"4.13.1.2": "etcsBaseline", "4.3.1": "temperatureRange", "4.5.1.1": "compatibleWithLineCategory",
           "4.13.2.9": "etcsDataCommApp", "4.6.2": "hasCantDeficiencyCompensation",
           "4.7.3": "hasMandatoryParkingBrake", "4.10.15": "vehicleContactForce", "4.1.3": "wheelSetGauge",
           "4.3.2": "altitudeRange", "0.1": "vehicleTypeNumber"}
_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_RESTRICTION = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*?)(?::\s*(.*))?$")


def nn(iri: str) -> ox.NamedNode:
    return ox.NamedNode(iri)


def lit(v, dt: str | None = None, lang: str | None = None) -> ox.Literal:
    if lang:
        return ox.Literal(str(v), language=lang)
    if dt:
        return ox.Literal(str(v), datatype=nn(dt))
    return ox.Literal(str(v))


def slug(s: str, limit: int = 60) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"
    if len(out) > limit:
        out = out[:limit].rstrip("-") + "-" + hashlib.sha1(s.encode()).hexdigest()[:8]
    return out


def number(s: str) -> float | None:
    """First number in an ERATV value: '0004.60' -> 4.6, 'Single: 00010,100' -> 10.1."""
    m = _NUM.search(s or "")
    return float(m.group(0).replace(",", ".")) if m else None


def _unit_code(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip().replace("Ν", "N").replace("Α", "A")   # Greek capitals seen in the data
    return _UNITS.get(u.lower()) or _UNITS.get(u)


def convert_unit(value: float, given: str | None, target: str | None) -> float:
    """`value` in the ERATV unit `given` expressed in the ontology unit `target` (QUDT IRI)."""
    src = _unit_code(given)
    tgt = target.rsplit("/", 1)[-1] if target else None
    if not src or not tgt or src == tgt:
        return value
    a, b = _SCALE.get(src), _SCALE.get(tgt)
    if a and b and a[0] == b[0]:
        return value * a[1] / b[1]
    return value


def _fmt(x: float, dt: str) -> str:
    if dt.endswith("#integer"):
        return str(int(round(x)))
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def boolean(s: str) -> bool | None:
    v = (s or "").strip().lower()
    if v in ("true", "yes", "y"):
        return True
    if v in ("false", "no", "n"):
        return False
    return None


def _date(s: str | None) -> str | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def countries(s: str | None) -> list[str]:
    """'SE(Sweden)' / 'Sweden(SE)' / 'AT(Austria), DE(Germany)' -> country authority IRIs."""
    out: list[str] = []
    for code in re.findall(r"\b([A-Z]{2})\b", s or ""):
        if code in COUNTRIES and COUNTRY + COUNTRIES[code] not in out:
            out.append(COUNTRY + COUNTRIES[code])
    return out


@dataclass
class Result:
    type_id: str
    iri: str
    triples: list[ox.Triple] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    unmapped: list[dict] = field(default_factory=list)
    mapped: int = 0

    def turtle(self) -> str:
        prefixes = dict(PREFIXES)
        for tr in self.triples:          # one prefix per ERA concept list, so names stay readable
            o = tr.object
            if isinstance(o, ox.NamedNode) and o.value.startswith(ERA + "concepts/"):
                ns = o.value.rsplit("/", 1)[0] + "/"
                prefixes.setdefault("era-" + slug(ns[len(ERA + "concepts/"):]), ns)
        return ox.serialize(self.triples, format=ox.RdfFormat.TURTLE, prefixes=prefixes).decode("utf-8")

    @property
    def coverage(self) -> float:
        total = self.mapped + len(self.unmapped)
        return self.mapped / total if total else 1.0


class _Converter:
    def __init__(self, rec: Record, vocab: Vocab, state: str | None, last_update: str | None):
        self.rec, self.v = rec, vocab
        tid = rec.type_id
        self.res = Result(tid, VT + tid)
        self.vt = nn(self.res.iri)
        self.state, self.last_update = state, last_update
        self.cps: dict[str, ox.NamedNode] = {}
        self.cfgs: set[str] = set()

    # -- helpers -------------------------------------------------------------
    def add(self, s, p: str, o) -> None:
        self.res.triples.append(ox.Triple(s, nn(p), o))

    def miss(self, p: Param, value: str, why: str) -> None:
        self.res.unmapped.append({"code": p.code, "name": p.name, "config": p.config, "value": value, "why": why})

    def concept(self, scheme: str | None, value: str) -> str | None:
        c = self.v.concept(scheme, value)
        if c:
            return c
        bare = re.sub(r"\s*\(.*?\)\s*", " ", value).strip()     # 'ATC v2 (via STM ...)' -> 'ATC v2'
        if bare and bare != value:
            return self.v.concept(scheme, bare)
        return None

    def concepts(self, scheme: str | None, value: str) -> list[str]:
        c = self.concept(scheme, value)
        if c:
            return [c]
        parts = [x for x in re.split(r"\s*(?:\+|;|,|/| and )\s*", value) if x]
        if len(parts) > 1:
            found = [self.concept(scheme, x) for x in parts]
            if all(found):
                return list(dict.fromkeys(found))
        return []

    def configuration(self, name: str) -> ox.NamedNode:
        iri = nn(VT + "configurations/cfg-" + slug(name))
        if iri.value in self.cfgs:
            return iri
        self.cfgs.add(iri.value)
        self.add(iri, RDF + "type", nn(ERA + "VehicleTypeConfiguration"))
        self.add(iri, RDFS + "label", lit(name))
        parts = [x.strip() for x in name.split(" / ")]
        rest = list(parts)
        if rest and re.match(r"^\d{3,4}\s*mm$", rest[0]):
            g = self.v.concept(GAUGE_SCHEME, rest.pop(0))
            if g:
                self.add(iri, ERA + "forWheelSetGauge", nn(g))
        if rest:
            e = self.v.concept(ENERGY_SCHEME, rest[0])
            if e:
                rest.pop(0)
                self.add(iri, ERA + "forEnergySupplySystem", nn(e))
        for x in rest:
            b = self.concept(TPLS_SCHEME, x)
            if b:
                self.add(iri, ERA + "forOnBoardClassB", nn(b))
        c = self.v.concept(CONFIG_SCHEME, name)
        if c:
            self.add(iri, RDFS + "seeAlso", nn(c))
        return iri

    def param_set(self, config: str | None) -> ox.NamedNode:
        key = config or ""
        if key not in self.cps:
            iri = nn(VT + "configParameterSets/cps-" + slug(f"{self.rec.type_id}-{config or 'all'}", 120))
            self.cps[key] = iri
            self.add(iri, RDF + "type", nn(ERA + "VehicleTypeConfigParameterSet"))
            self.add(iri, RDFS + "label", lit(f"{self.rec.type_id}: {config or 'all configurations'}"))
            if config:
                self.add(iri, ERA + "hasVehicleTypeConfiguration", self.configuration(config))
            self.add(self.vt, ERA + "configDependentParameter", iri)
        return self.cps[key]

    def subject(self, p: Param, prop: Prop | None) -> list[ox.NamedNode]:
        """Where a value goes: its configuration's parameter set, or the vehicle type."""
        if p.config:
            return [self.param_set(p.config)]
        if prop is not None and prop.domain == ERA + "VehicleTypeConfigParameterSet":
            configs = self.rec.configurations()
            return [self.param_set(c) for c in configs] if configs else [self.param_set(None)]
        return [self.vt]

    def emit(self, p: Param, prop: Prop, value: str, unit: str | None) -> bool:
        """One value of a parameter to `prop` according to its range. False if it does not fit."""
        rng = prop.range or ""
        objs: list = []
        if prop.is_concept:
            if value.strip().lower() in ("none", "not applicable", "-", ""):
                cs = self.concepts(prop.scheme, value)
                if not cs:
                    return True                                   # nothing installed: no value
            else:
                cs = self.concepts(prop.scheme, value)
            if not cs:
                return False
            objs = [nn(c) for c in cs]
        elif rng == XSD + "boolean":
            b = boolean(value)
            if b is None:
                return False
            objs = [lit("true" if b else "false", XSD + "boolean")]
        elif rng in (XSD + "integer", XSD + "decimal", XSD + "double", XSD + "float"):
            x = number(value)
            if x is None:
                return False
            x = convert_unit(x, unit, prop.unit)
            objs = [lit(_fmt(x, rng), rng)]
        elif rng == XSD + "date":
            d = _date(value)
            if not d:
                return False
            objs = [lit(d, XSD + "date")]
        elif rng == RDF + "langString":
            objs = [lit(value, lang="en")]
        elif not rng or rng in (XSD + "string", RDFS + "Literal"):
            objs = [lit(value)]
        else:
            return False                                          # an object property we do not build
        for s in self.subject(p, prop):
            for o in objs:
                self.add(s, prop.iri, o)
        return True

    def choose(self, code: str) -> Prop | None:
        props = [p for p in self.v.props(code) if not p.local.endswith("Comment")]
        if code in _PREFER:
            hit = next((p for p in props if p.local == _PREFER[code]), None)
            if hit:
                return hit
        if len(props) == 1:
            return props[0]
        on_vt = [p for p in props if p.domain in (ERA + "VehicleType", ERA + "VehicleTypeConfigParameterSet")]
        return on_vt[0] if len(on_vt) == 1 else None

    # -- parameters ----------------------------------------------------------
    def generic(self, p: Param) -> None:
        prop = self.choose(p.code or "")
        for value, unit in p.pairs():
            if not value:
                continue
            if prop is None:
                self.miss(p, value, "no ontology property" if not self.v.props(p.code or "") else
                          "several ontology properties")
            elif self.emit(p, prop, value, unit):
                self.res.mapped += 1
            else:
                self.miss(p, value, "value does not fit " + prop.local)

    def concept_with_comment(self, p: Param, prop_local: str, comment_local: str) -> None:
        prop = self.v.prop(prop_local)
        for value, _ in p.pairs():
            if not value or value.strip().lower() == "none":
                continue
            if prop and self.emit(p, prop, value, None) and self.concepts(prop.scheme, value):
                self.res.mapped += 1
            else:
                for s in self.subject(p, prop):
                    self.add(s, ERA + comment_local, lit(value))
                self.miss(p, value, f"no concept in {(prop.scheme if prop else '?').rsplit('/', 1)[-1]}")

    def special(self, p: Param) -> None:
        code = p.code
        vals = [v for v in p.values if v]
        if code in ("0.1", "0.2", "0.3", "0.4") or not vals:
            return                                    # identifiers are written from the Type ID
        if code == "1.1":
            self.add(self.vt, RDFS + "label", lit(vals[0]))
        elif code == "1.2":
            for v in vals:
                self.add(self.vt, ERA + "alternativeTypeName", lit(v, lang="en"))
        elif code in ("1.4", "1.5"):
            scheme, prop = (CATEGORY_SCHEME, "vehicleCategory") if code == "1.4" else (SUBCATEGORY_SCHEME, "vehicleSubCategory")
            c = self.concept(scheme, vals[0])
            if c:
                self.add(self.vt, ERA + prop, nn(c))
                self.res.mapped += 1
            else:
                self.miss(p, vals[0], "no concept")
        elif code == "4.10.5":
            nums = [convert_unit(x, u, "M") for x, u in ((number(v), u) for v, u in p.pairs()) if x is not None]
            if nums:
                for s in self.subject(p, None):
                    self.add(s, ERA + "minimumContactWireHeight", lit(_fmt(min(nums), XSD + "double"), XSD + "double"))
                    self.add(s, ERA + "maximumContactWireHeight", lit(_fmt(max(nums), XSD + "double"), XSD + "double"))
                self.res.mapped += 1
        elif code == "4.10.8":
            # 'Single: 00010,100' / 'Multiple: 00009,200' (m): the ontology has one string
            single = next((number(v) for v in vals if v.lower().startswith("single")), None)
            if single is None:
                single = number(vals[0])
            if single is not None:
                prop = self.v.prop("shortestDistanceBetweenPantographsInContactWithOCL")
                for s in self.subject(p, prop):
                    self.add(s, ERA + "shortestDistanceBetweenPantographsInContactWithOCL", lit(_fmt(single, XSD + "decimal")))
                self.res.mapped += 1
        elif code == "4.5.3.4":
            text = " ".join(vals)
            node = nn(VT + "axleSpacings/" + slug(self.rec.type_id + "-" + (p.config or "all"), 120))
            found = False
            for letter in "abc":
                m = re.search(rf"\b{letter}\s*:\s*([-+]?\d+(?:[.,]\d+)?)", text)
                if m:
                    found = True
                    self.add(node, ERA + f"axleSpacingDistance{letter.upper()}",
                             lit(_fmt(float(m.group(1).replace(",", ".")), XSD + "decimal"), XSD + "decimal"))
            if found:
                self.add(node, RDF + "type", nn(ERA + "AxleSpacing"))
                if p.explanation:
                    self.add(node, ERA + "axleSpacingExplanation", lit(p.explanation))
                self.add(self.vt, ERA + "axleSpacingDistance", node)
                self.res.mapped += 1
            else:
                self.miss(p, text, "axle spacing not understood")
        elif code == "4.6.4":
            nums = [(number(v), (u or "").strip().lower()) for v, u in p.pairs()]
            speed = next((x for x, u in nums if x is not None and "km" in u), None)
            cant = next((x for x, u in nums if x is not None and u == "mm"), None)
            if speed is None and nums and nums[0][0] is not None:
                speed = nums[0][0]
            if speed is not None:
                for i, s in enumerate(self.subject(p, self.v.prop("vehicleTypeMaximumSpeedAndCantDeficiency"))):
                    node = nn(VT + "maximumSpeedAndCantDeficiencies/" + slug(f"{self.rec.type_id}-{p.config or 'all'}-{i}", 120))
                    self.add(node, RDF + "type", nn(ERA + "MaximumSpeedAndCantDeficiency"))
                    self.add(node, ERA + "vehicleTypeMaximumSpeed", lit(_fmt(speed, XSD + "integer"), XSD + "integer"))
                    if cant is not None:
                        self.add(node, ERA + "vehicleTypeMaximumCantDeficiency", lit(_fmt(cant, XSD + "integer"), XSD + "integer"))
                    self.add(s, ERA + "vehicleTypeMaximumSpeedAndCantDeficiency", node)
                self.res.mapped += 1
        elif code in ("4.7.5", "4.7.7"):
            self.braking(p, "emergencyBrakeProfile" if code == "4.7.5" else "serviceBrakingProfile")
        elif code == "4.7.6":
            self.add(self.vt, ERA + "brakeWeightPercentage", lit(" ".join(v for v in vals if v.strip(", "))))
            self.res.mapped += 1
        elif code == "4.13.1.5":
            self.concept_with_comment(p, "protectionLegacySystem", "protectionLegacySystemComment")
        elif code == "4.13.2.3":
            self.concept_with_comment(p, "legacyRadioSystem", "legacyRadioSystemComment")
        elif code == "4.5.1":
            for v in vals:
                self.miss(p, v, "permissible payload table not converted yet")

    def braking(self, p: Param, prop: str) -> None:
        """'a: 0956,00 m', '0001,03 m/s²', 'b: ...': one era:BrakingProfile per letter."""
        profiles: list[dict] = []
        for v, u in p.pairs():
            m = re.match(r"^\s*([a-z])\s*:", v)
            if m or not profiles:
                profiles.append({"name": m.group(1) if m else ""})
            x = number(v)
            if x is None:
                continue
            uu = (u or "").lower()
            if "s" in uu and "/" in uu:
                profiles[-1]["dec"] = x
            elif uu.strip() in ("m", "") and "dist" not in profiles[-1]:
                profiles[-1]["dist"] = x
            elif uu.strip() in ("kg", "t"):
                profiles[-1]["mass"] = convert_unit(x, u, "KiloGM")
        n = 0
        for i, pr in enumerate(profiles):
            if "dec" not in pr and "dist" not in pr:
                continue
            node = nn(VT + "brakingProfiles/" + slug(f"{self.rec.type_id}-{prop}-{pr['name'] or i}", 120))
            self.add(node, RDF + "type", nn(ERA + "BrakingProfile"))
            if "dec" in pr:
                self.add(node, ERA + "profileDeceleration", lit(_fmt(pr["dec"], XSD + "decimal"), XSD + "decimal"))
            if "dist" in pr:
                self.add(node, ERA + "profileStoppingDistance", lit(_fmt(pr["dist"], XSD + "integer"), XSD + "integer"))
            if "mass" in pr:
                self.add(node, ERA + "profileLoadConditionMass", lit(_fmt(pr["mass"], XSD + "integer"), XSD + "integer"))
            if pr["name"]:
                self.add(node, RDFS + "label", lit(f"load condition {pr['name']}"))
            self.add(self.vt, ERA + prop, node)
            n += 1
        if n:
            self.res.mapped += 1
        else:
            self.miss(p, " ".join(p.values), "braking profile not understood")

    # -- authorisations ------------------------------------------------------
    def authorisation(self, i: int, a) -> dict:
        tid = self.rec.type_id
        key = slug(f"{tid}-{a.title or i}", 120)
        case = nn(VT + "authorisations/cases/vtac-" + key)
        auth = nn(VT + "authorisations/auth-" + key)
        app = nn(VT + "authorisations/applications/vtapp-" + key)
        self.add(app, RDF + "type", nn(ERA + "VehicleTypeAuthorisationApplication"))
        self.add(app, VPA + "constitutedBy", case)
        self.add(app, VPA + "requestFor", auth)
        self.add(case, RDF + "type", nn(ERA + "VehicleTypeAuthorisationCase"))
        self.add(case, VPA + "concerns", self.vt)
        self.add(auth, RDF + "type", nn(ERA + "VehicleTypeAuthorisation"))
        self.add(auth, RDFS + "label", lit(f"Authorisation of {tid} ({a.title})", lang="en"))
        out: dict = {"title": a.title, "areaOfUse": [], "restrictions": [], "nonCoded": []}

        reg = self.rec.by_name("Registration Method")
        if reg:
            c = self.concept(REGMETHOD_SCHEME, reg)
            if c:
                self.add(case, ERA + "typeRegistrationMethod", nn(c))
        configs = set(self.rec.configurations())
        for p in a.params:
            vals = [v for v in p.values if v]
            if not vals:
                continue
            name = (p.name or "").lower()
            if p.code == "3.0":
                for v in vals:
                    for c in countries(v):
                        self.add(case, ERA + "areaOfUse", nn(c))
                    out["areaOfUse"].append(v)
            elif p.code == "3.1.1":
                cs = countries(vals[0])
                if len(cs) == 1:          # an EU authorisation lists every country of its area of use
                    self.add(case, ERA + "authorisingMemberState", nn(cs[0]))
                out["memberState"] = vals[0]
            elif p.code == "3.1.2.1":
                self.add(case, ATLAS + "authorisationStatus", lit(vals[0]))
                out["status"] = vals[0]
            elif p.code == "3.1.2.2":
                d = _date(vals[0])
                if d:
                    self.add(auth, ATLAS + "validUntil", lit(d, XSD + "date"))
                    out["validUntil"] = d
            elif p.code == "3.1.2.3":
                for j, v in enumerate(vals):
                    self.restriction(case, p.config, j, v, out)
            elif p.code == "3.1.2.4":
                for v in vals:
                    if v in configs or v in out["nonCoded"]:
                        continue
                    out["nonCoded"].append(v)
                    self.add(case, ERA + "nonCodedRestrictions", lit(v))
            elif "name of organisation" in name:
                self.add(auth, ATLAS + "holderName", lit(vals[0]))
                out["holder"] = vals[0]
            elif "registered business number" in name:
                self.add(auth, ATLAS + "holderRegistrationNumber", lit(vals[0]))
            elif "authorisation document reference" in name:
                self.add(auth, DCT + "identifier", lit(vals[0].strip()))
                out["ein"] = vals[0].strip()
            elif "date of" in name and "authorisation" in name:
                d = _date(vals[0])
                if d:
                    self.add(auth, DCT + "issued", lit(d, XSD + "date"))
                    out.setdefault("issued", d)
        return out

    def restriction(self, case, config: str | None, j: int, text: str, out: dict) -> None:
        m = _RESTRICTION.match(text.strip())
        if not m:
            return
        code, label, value = m.group(1), m.group(2), (m.group(3) or "").strip()
        if not value and code.count(".") == 0:
            return                                     # a heading such as "1 Technical restriction ..."
        node = nn(VT + "authorisations/restrictions/res-" + slug(f"{case.value.rsplit('/', 1)[-1]}-{config or 'all'}-{j}", 140))
        self.add(node, RDF + "type", nn(ERA + "VehicleTypeAuthorisationRestriction"))
        self.add(node, RDFS + "label", lit(text.strip()))
        self.add(node, ATLAS + "restrictionCode", lit(code))
        if value:
            self.add(node, ATLAS + "restrictionValue", lit(value))
        if config:
            self.add(node, ERA + "forConfiguration", self.configuration(config))
        self.add(case, ATLAS + "restriction", node)
        r = {"code": code, "label": label, "value": value or None, "config": config}
        if code == "1.1":
            # '100 m in depot / 150 m for revenue service': the highest figure is the safe one
            nums = [float(n.replace(",", ".")) for n in _NUM.findall(value)]
            x = max(nums) if nums else None
            if x is not None:
                self.add(node, ATLAS + "minimumCurveRadius", lit(_fmt(x, XSD + "decimal"), XSD + "decimal"))
                r["minimumCurveRadius"] = x
        elif code == "1.3":
            x = number(value)
            if x is not None:
                self.add(node, ATLAS + "maximumSpeed", lit(_fmt(x, XSD + "integer"), XSD + "integer"))
                r["maximumSpeed"] = x
        out["restrictions"].append(r)

    # -- whole record --------------------------------------------------------
    def run(self) -> Result:
        rec, vt, tid = self.rec, self.vt, self.rec.type_id
        self.add(vt, RDF + "type", nn(ERA + "VehicleType"))
        self.add(vt, DCT + "identifier", lit(tid))
        parts = tid.split("-")
        number_ = "-".join(parts[:4]) if len(parts) >= 4 else tid
        self.add(vt, ERA + "vehicleTypeNumber", lit(number_))
        if len(parts) > 4:
            self.add(vt, ERA + "includedVersionsVariant", lit("-".join(parts[4:])))
        self.add(vt, RDFS + "seeAlso", nn(VIEW_URL + tid))
        self.add(vt, DCT + "source", nn(SOURCE))
        if self.state:
            self.add(vt, ATLAS + "eratvState", lit(self.state))
        if self.last_update:
            d = _date(self.last_update)
            if d:
                self.add(vt, DCT + "modified", lit(d, XSD + "date"))
        prev = rec.by_name("Registered Vehicle Type")
        if prev and prev.strip() != tid:
            self.add(vt, ERA + "previousVehicleType", nn(VT + prev.strip()))
        platform = rec.by_name("Platform")
        if platform:
            self.add(vt, ERA + "vehicleTypePlatform", lit(platform))
        manufacturer = rec.first("1.3.1.1")
        if manufacturer:
            self.add(vt, ATLAS + "manufacturerName", lit(manufacturer))
        mode = rec.by_name("Exceptional Mode")
        if mode:
            self.add(vt, ATLAS + "exceptionalMode", lit(mode))

        for p in rec.params:
            if p.section == "tsi" or not p.code:
                continue
            if p.code.startswith("1.3"):
                continue                               # manufacturer (above; contact data dropped)
            if p.code in _SPECIAL:
                self.special(p)
            else:
                self.generic(p)
        # TSI conformity: kept as text per configuration (no ontology property)
        tsi = []
        for p in rec.params:
            if p.section == "tsi" and p.code == "2.1":
                tsi.append({"config": p.config, "tsi": p.tsi, "notComplied": p.values})
                for t in p.tsi:
                    s = self.param_set(p.config) if p.config else vt
                    self.add(s, ATLAS + "conformsToTSI", lit(t))
        auths = [self.authorisation(i, a) for i, a in enumerate(rec.authorisations, 1)]

        self.res.summary = {
            "typeId": tid, "typeNumber": number_, "state": self.state, "lastUpdate": self.last_update,
            "name": rec.first("1.1"), "altName": rec.first("1.2"), "category": rec.first("1.4"),
            "subcategory": rec.first("1.5"), "manufacturer": manufacturer, "platform": platform,
            "previous": prev, "configurations": rec.configurations(), "tsi": tsi, "authorisations": auths,
            "maxDesignSpeed": number(rec.first("4.1.2.1") or ""),
            "minHorizontalRadius": number(rec.first("4.8.4") or ""),
            "coverage": round(self.res.coverage, 3), "unmapped": len(self.res.unmapped),
            "url": VIEW_URL + tid,
        }
        return self.res


def convert(rec: Record, vocab: Vocab, state: str | None = "PUBLISHED", last_update: str | None = None) -> Result:
    if vocab.is_empty():
        raise ValueError("the ERA ontology with era:eratvIndex is not loaded (mirror an environment first)")
    if not rec.type_id:
        raise ValueError("the record has no Type ID")
    return _Converter(rec, vocab, state, last_update).run()
