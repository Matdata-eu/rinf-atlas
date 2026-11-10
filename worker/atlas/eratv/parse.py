"""Reading the XML export of one ERATV vehicle type (`ExportVehicleType/{id}?exportTo=XML`).

The export has no schema. What the parser relies on:

* the sections GeneralInfo, ConformityWithTSI, Authorisations/Authorisation[@Title]
  and TechnicalCharacteristics;
* a parameter is a `Name`, an optional `Code` (the ERATV parameter number, sometimes
  followed by the label, as in "3.0 Area Of Use:") and its `Value`s, each optionally
  followed by a `Unit`;
* one `Characteristic` element often holds several parameters in a row (Name, Code,
  Configurations, Name, Code, Configurations, ...), so children are read as a sequence;
* values that depend on the configuration sit in `Configurations/Characteristic`, whose
  `Name` is the configuration ("1435mm / AC 15kV-16.7Hz / ATC v2"), only the energy
  supply system ("AC 15kV-16.7Hz") or absent (the value holds for every configuration).

Contact data of the manufacturer and the authorisation holder (address, e-mail, ...)
is dropped here: the vehicle check does not need it.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Contact data: 1.3.2.x (manufacturer) and 3.1.3.N.2.2.x / 3.1.3.N.M.2.x (holder).
_CONTACT = re.compile(r"^(1\.3\.2|3\.1\.3\.\d+\.2\.2|3\.1\.3\.\d+\.\d\.2)(\.\d+)?$")
_CODE = re.compile(r"^\s*(\d+(?:\.\d+)*(?:\.[a-z])?)\s*(.*?)\s*:?\s*$")
_FILENAME = re.compile(r"VehicleType_(?P<id>[^_]+)_(?P<state>[A-Z]+)\.xml", re.I)


@dataclass
class Param:
    section: str                 # general | tsi | technical | authorisation
    code: str | None             # ERATV parameter number, e.g. "4.10.5"
    name: str
    config: str | None = None    # configuration or energy supply system the values are for
    values: list[str] = field(default_factory=list)
    units: list[str | None] = field(default_factory=list)   # one per value
    tsi: list[str] = field(default_factory=list)
    explanation: str | None = None

    def pairs(self) -> list[tuple[str, str | None]]:
        return list(zip(self.values, self.units))


@dataclass
class Authorisation:
    title: str
    params: list[Param] = field(default_factory=list)

    def get(self, code: str) -> Param | None:
        return next((p for p in self.params if p.code == code), None)

    def first(self, code: str) -> str | None:
        p = self.get(code)
        return p.values[0] if p and p.values else None


@dataclass
class Record:
    type_id: str
    params: list[Param] = field(default_factory=list)
    authorisations: list[Authorisation] = field(default_factory=list)

    def all(self, code: str) -> list[Param]:
        return [p for p in self.params if p.code == code]

    def first(self, code: str) -> str | None:
        for p in self.all(code):
            if p.values:
                return p.values[0]
        return None

    def by_name(self, name: str) -> str | None:
        p = next((p for p in self.params if p.name.lower() == name.lower() and p.values), None)
        return p.values[0] if p else None

    def configurations(self) -> list[str]:
        """Configuration names in order of appearance (gauge / energy supply / class B ...)."""
        seen: dict[str, None] = {}
        for p in self.params:
            if p.config and _is_full_config(p.config):
                seen.setdefault(p.config, None)
        for a in self.authorisations:
            for p in a.params:
                if p.config and _is_full_config(p.config):
                    seen.setdefault(p.config, None)
        return list(seen)


def _is_full_config(name: str) -> bool:
    return bool(re.match(r"^\d{3,4}\s*mm\b", name.strip()))


def state_from_filename(disposition: str | None) -> tuple[str | None, str | None]:
    """(Type ID, state) from a Content-Disposition such as
    `attachment; filename=VehicleType_11-256-0001-3-002-001_PUBLISHED.xml`."""
    m = _FILENAME.search(disposition or "")
    return (m.group("id"), m.group("state").upper()) if m else (None, None)


def _text(e: ET.Element) -> str:
    return re.sub(r"\s+", " ", (e.text or "")).strip()


def _split_code(raw: str) -> tuple[str | None, str]:
    m = _CODE.match(raw or "")
    if not m:
        return None, raw.strip()
    return m.group(1), m.group(2).strip()


class _Builder:
    def __init__(self, section: str, out: list[Param]):
        self.section, self.out = section, out

    def walk(self, elem: ET.Element) -> None:
        cur: Param | None = None

        def flush():
            nonlocal cur
            if cur is not None and (cur.values or cur.tsi) and not (cur.code and _CONTACT.match(cur.code)):
                self.out.append(cur)
            cur = None

        for c in elem:
            tag = c.tag
            if tag == "Name":
                flush()
                cur = Param(self.section, None, _text(c))
            elif tag == "Code":
                if cur is None:
                    cur = Param(self.section, None, "")
                code, label = _split_code(c.text or "")
                cur.code = code
                if label and not cur.name:
                    cur.name = label
            elif tag == "Value":
                if cur is None:
                    cur = Param(self.section, None, "")
                cur.values.append(_text(c))
                cur.units.append(None)
            elif tag == "Unit":
                if cur is not None and cur.units and cur.units[-1] is None:
                    cur.units[-1] = _text(c) or None
            elif tag == "TSI":
                if cur is None:
                    cur = Param(self.section, None, "")
                cur.tsi.append(_text(c))
            elif tag == "Explanations":
                if cur is not None:
                    cur.explanation = " ".join(_text(x) for x in c.iter() if _text(x)) or None
            elif tag == "Configurations":
                base = cur or Param(self.section, None, "")
                for cc in c:
                    if cc.tag != "Characteristic":
                        continue
                    p = Param(self.section, base.code, base.name, config=None)
                    for x in cc:
                        if x.tag == "Name":
                            p.config = _text(x) or None
                        elif x.tag == "Value":
                            p.values.append(_text(x))
                            p.units.append(None)
                        elif x.tag == "Unit":
                            if p.units and p.units[-1] is None:
                                p.units[-1] = _text(x) or None
                        elif x.tag == "TSI":
                            p.tsi.append(_text(x))
                        elif x.tag == "Explanations":
                            p.explanation = " ".join(_text(y) for y in x.iter() if _text(y)) or None
                    if (p.values or p.tsi) and not (p.code and _CONTACT.match(p.code)):
                        self.out.append(p)
                # the values went into the configurations; what follows is a new parameter
                cur = None
            elif tag in ("Characteristic", "Type"):
                flush()
                self.walk(c)
        flush()


def parse(xml: bytes | str) -> Record:
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    if xml.startswith(b"\xef\xbb\xbf"):
        xml = xml[3:]
    root = ET.fromstring(xml)
    if root.tag != "VehicleType":
        raise ValueError(f"not an ERATV vehicle type export (root element {root.tag!r})")
    type_el = root.find("GeneralInfo/Type")
    type_id = (type_el.get("ID") if type_el is not None else None) or ""
    rec = Record(type_id=type_id.strip())
    for section, path in (("general", "GeneralInfo"), ("tsi", "ConformityWithTSI"),
                          ("technical", "TechnicalCharacteristics")):
        el = root.find(path)
        if el is not None:
            _Builder(section, rec.params).walk(el)
    for a in root.findall("Authorisations/Authorisation"):
        auth = Authorisation(title=(a.get("Title") or "").strip())
        _Builder("authorisation", auth.params).walk(a)
        rec.authorisations.append(auth)
    if not rec.type_id:
        rec.type_id = rec.first("0.1") or ""
    return rec
