// Icons for the classes of the ERA ontology: one outline icon per class a person meets on a
// page, drawn on a 24 px grid with a 1.5 px round stroke in currentColor. The same file is
// in RINF Describe and RINF Atlas (web/src/class-icons.ts): change both together.
// A class without its own icon takes the icon of its nearest superclass in the ontology
// (listed here as well), so subclasses of a drawn class need no entry.

const ERA = 'http://data.europa.eu/949/';
const SKOS = 'http://www.w3.org/2004/02/skos/core#';
const ORG = 'http://www.w3.org/ns/org#';
const FOAF = 'http://xmlns.com/foaf/0.1/';

/** The drawing of each icon: the inside of a 24×24 SVG. */
export const ICONS: Record<string, string> = {
  'section-of-line': '<circle cx="5" cy="12" r="2.5"/><circle cx="19" cy="12" r="2.5"/><path d="M7.5 12h9"/>',
  'operational-point': '<path d="M2 12h7.5M14.5 12H22M13.77 13.77 17 17h5"/><circle cx="12" cy="12" r="2.5"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>',
  'national-line': '<path d="M3 16h18M3 16V8M21 16V8M7.5 16v-3M12 16v-5M16.5 16v-3"/>',
  'kilometric-post': '<rect x="7" y="3" width="10" height="9" rx="1.5"/><path d="M10 6.5h4M10 9h2.5M12 12v9M9 21h6"/>',
  'reference-border-point': '<path d="M12 3v5.5M12 15.5V21" stroke-dasharray="2 2.5"/><path d="M2 12h7.5M14.5 12H22"/><circle cx="12" cy="12" r="2.5"/>',
  'tent-corridor': '<path d="M2.75 7.5h18.5M2.75 16.5h18.5M7.5 9.5 10 12l-2.5 2.5M13 9.5l2.5 2.5-2.5 2.5"/>',
  'primary-location': '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.25"/>',
  'subsidiary-location': '<path d="M9 19s-5-4.3-5-8.5a5 5 0 0 1 10 0c0 4.2-5 8.5-5 8.5z"/><circle cx="9" cy="10.5" r="1.75"/><path d="M13.5 15.5l2.6 1.3" stroke-dasharray="1.5 2"/><circle cx="18.5" cy="18" r="2"/>',
  'linear-element': '<path d="M6.5 17.5 17.5 6.5M10.5 9.5h4v4"/><circle cx="5" cy="19" r="2" fill="currentColor" stroke="none"/><circle cx="19" cy="5" r="2" fill="currentColor" stroke="none"/>',
  'net-relation': '<path d="M3 17h5.5M15.5 17 20 12.5"/><circle cx="10" cy="17" r="1.75" fill="currentColor" stroke="none"/><circle cx="14" cy="17" r="1.75" fill="currentColor" stroke="none"/><path d="M9 12.5a4 4 0 0 1 6 0M13.2 10.6l1.8 1.9-2.5.6"/>',
  'track': '<path d="M2 9h20M2 15h20M4.5 6.5v11M9.5 6.5v11M14.5 6.5v11M19.5 6.5v11"/>',
  'running-track': '<path d="M2 9h14M2 15h14M4.5 6.5v11M9 6.5v11M13.5 6.5v11M18 8l4 4-4 4"/>',
  'siding': '<path d="M2 9h14M2 15h14M4.5 6.5v11M9 6.5v11M13.5 6.5v11"/><rect x="16.5" y="6" width="4" height="12" rx="1"/>',
  'switch': '<path d="M2 16h20M6 16l6-6h10"/>',
  'platform-edge': '<rect x="3" y="4" width="18" height="7.5" rx="1.5"/><path d="M6 8.5h12" stroke-dasharray="0.01 3"/><path d="M2 15.5h20M2 20h20"/>',
  'level-crossing': '<path d="M5.5 4l13 9M18.5 4l-13 9M12 8.5V21M9 21h6"/>',
  'tunnel': '<path d="M2 20h20M3 20v-8a9 9 0 0 1 18 0v8M7 20v-7a5 5 0 0 1 10 0v7M10.5 20l.75-4M13.5 20l-.75-4"/>',
  'bridge': '<path d="M2 9h20M4 9v11M20 9v11M4 20a8 8 0 0 1 16 0M8 9v4M12 9v3M16 9v4"/>',
  'signal': '<rect x="8.5" y="2.5" width="7" height="11.5" rx="3.5"/><circle cx="12" cy="6" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="10.5" r="1.5"/><path d="M12 14v7M9 21h6"/>',
  'habd': '<path d="M2 20.5h20"/><circle cx="9" cy="14.5" r="5"/><circle cx="9" cy="14.5" r="1.5"/><path d="M17 5.5c-1 1.25 1 2.5 0 3.75s1 2.5 0 3.75M20.5 5.5c-1 1.25 1 2.5 0 3.75s1 2.5 0 3.75"/>',
  'special-area': '<path d="M2 12h20"/><rect x="6" y="6" width="12" height="12" rx="2" stroke-dasharray="3 2.5"/>',
  'special-tunnel-area': '<rect x="3.5" y="4.5" width="17" height="15" rx="2" stroke-dasharray="3 2.5"/><path d="M7.5 16v-3a4.5 4.5 0 0 1 9 0v3M2 16h20"/>',
  'infrastructure-element': '<path d="M8.5 3.5h7v4h-2v8l5.5 2.5v2.5H5V18l5.5-2.5v-8h-2z"/>',
  'contact-line-system': '<path d="M4 3v18M20 3v18M2.5 21h3M18.5 21h3M4 5q8 5 16 0M4 11.5h16M8 6.6v4.9M12 7.5v4M16 6.6v4.9"/>',
  'train-detection-system': '<path d="M4.5 13h15M4.5 19h15M3 11.5v3M21 11.5v3M3 17.5v3M21 17.5v3M7 13V5h2.5M14.5 5H17v8"/><rect x="9.5" y="3" width="5" height="4" rx="1"/>',
  'etcs': '<path d="M2 20.5h20M9.5 12a3.5 3.5 0 0 1 5 0M7 9a7 7 0 0 1 10 0M4.5 6a10.5 10.5 0 0 1 15 0"/><rect x="7.5" y="15" width="9" height="4" rx="1"/>',
  'radio-block-centre': '<path d="M12 9.5V21M8 21l4-11.5L16 21M9.5 17h5M8.8 4.8a4.5 4.5 0 0 0 0 6.4M15.2 4.8a4.5 4.5 0 0 1 0 6.4M6 2.5a8 8 0 0 0 0 11M18 2.5a8 8 0 0 1 0 11"/><circle cx="12" cy="8" r="1.25" fill="currentColor" stroke="none"/>',
  'common-characteristics-subset': '<path d="M12 3.5 3 8l9 4.5L21 8z"/><path d="M3 12.25l9 4.5 9-4.5M3 16.5l9 4.5 9-4.5"/>',
  'infrastructure-manager': '<path d="M3 8.5 12 4l9 4.5zM5.5 8.5v8M10 8.5v8M14 8.5v8M18.5 8.5v8M3 16.5h18M2 20.5h20"/>',
  'body': '<rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1M10.5 21v-3h3v3"/>',
  'organisation-role': '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M9 6V4h6v2"/><circle cx="8.5" cy="11.5" r="1.75"/><path d="M5.75 16.25c.5-1.4 1.5-2 2.75-2s2.25.6 2.75 2M14 11h4M14 14.5h3"/>',
  'document': '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6M9 9h2"/>',
  'temporal-feature': '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18M7 15.5h10M7 14v3M17 14v3"/>',
  'vehicle-type': '<path d="M3 17V9a3 3 0 0 1 3-3h10.5c2.5 0 4.5 2.5 4.5 5.5V17z"/><path d="M6 9.5h3v3H6zM11 9.5h3v3h-3zM3 14h18M2 21.5h20"/><circle cx="7" cy="19.25" r="1.25"/><circle cx="17" cy="19.25" r="1.25"/>',
  'concept': '<path d="M3 4.5v6.4c0 .4.16.78.44 1.06l8.6 8.6a1.5 1.5 0 0 0 2.12 0l6.4-6.4a1.5 1.5 0 0 0 0-2.12l-8.6-8.6A1.5 1.5 0 0 0 10.9 3H4.5A1.5 1.5 0 0 0 3 4.5z"/><circle cx="7.5" cy="7.5" r="1.25"/>',
  'concept-scheme': '<circle cx="12" cy="5" r="2"/><path d="M12 7v4M5 11h14M5 11v4M12 11v4M19 11v4"/><circle cx="5" cy="17" r="2"/><circle cx="12" cy="17" r="2"/><circle cx="19" cy="17" r="2"/>',
};

/** Class IRI → icon: the classes drawn, then subclasses that borrow their superclass's icon. */
export const CLASS_ICONS: Record<string, string> = {
  [`${ERA}SectionOfLine`]: 'section-of-line',
  [`${ERA}OperationalPoint`]: 'operational-point',
  [`${ERA}LinearPositioningSystem`]: 'national-line',
  [`${ERA}NationalRailwayLine`]: 'national-line',
  [`${ERA}KilometricPost`]: 'kilometric-post',
  [`${ERA}ReferenceBorderPoint`]: 'reference-border-point',
  [`${ERA}TENTCorridor`]: 'tent-corridor',
  [`${ERA}PrimaryLocation`]: 'primary-location',
  [`${ERA}SubsidiaryLocation`]: 'subsidiary-location',
  [`${ERA}LinearElement`]: 'linear-element',
  [`${ERA}LinearElementSection`]: 'linear-element',
  [`${ERA}NetElement`]: 'linear-element',
  [`${ERA}NetRelation`]: 'net-relation',
  [`${ERA}Track`]: 'track',
  [`${ERA}RunningTrack`]: 'running-track',
  [`${ERA}Siding`]: 'siding',
  [`${ERA}Switch`]: 'switch',
  [`${ERA}PlatformEdge`]: 'platform-edge',
  [`${ERA}LevelCrossing`]: 'level-crossing',
  [`${ERA}Tunnel`]: 'tunnel',
  [`${ERA}Bridge`]: 'bridge',
  [`${ERA}Signal`]: 'signal',
  [`${ERA}HABD`]: 'habd',
  [`${ERA}SpecialArea`]: 'special-area',
  [`${ERA}SpecialTunnelArea`]: 'special-tunnel-area',
  [`${ERA}InfrastructureElement`]: 'infrastructure-element',
  [`${ERA}Feature`]: 'infrastructure-element',
  [`${ERA}ContactLineSystem`]: 'contact-line-system',
  [`${ERA}TrainDetectionSystem`]: 'train-detection-system',
  [`${ERA}ETCS`]: 'etcs',
  [`${ERA}RadioBlockCenter`]: 'radio-block-centre',
  [`${ERA}CommonCharacteristicsSubset`]: 'common-characteristics-subset',
  [`${ERA}InfrastructureManager`]: 'infrastructure-manager',
  [`${ERA}Body`]: 'body',
  [`${ORG}Organization`]: 'body',
  [`${ERA}OrganisationRole`]: 'organisation-role',
  [`${ERA}Document`]: 'document',
  [`${FOAF}Document`]: 'document',
  [`${ERA}TemporalFeature`]: 'temporal-feature',
  [`${ERA}ParameterApplicability`]: 'temporal-feature',
  [`${ERA}VehicleType`]: 'vehicle-type',
  [`${ERA}Vehicle`]: 'vehicle-type',
  [`${SKOS}Concept`]: 'concept',
  [`${SKOS}ConceptScheme`]: 'concept-scheme',
  [`${ERA}NonLinearElement`]: 'linear-element',
  [`${ERA}Manufacturer`]: 'body',
  [`${ERA}VehicleKeeper`]: 'body',
  [`${ERA}VehicleSeries`]: 'vehicle-type',
  [`${ERA}VehicleSet`]: 'vehicle-type',
};

/** An icon as SVG markup, or '' for an unknown key. */
export function iconSvg(key: string, size = 16): string {
  const body = ICONS[key];
  return body
    ? `<svg class="cls-icon" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`
    : '';
}

/** The icon key of a class, or null when the class has none. */
export function classIconKey(cls: string | null | undefined): string | null {
  return (cls && CLASS_ICONS[cls]) || null;
}

/** The icon key for a resource of these classes: the first class that has one. */
export function iconKeyOf(classes: readonly string[]): string | null {
  for (const c of classes) { const k = classIconKey(c); if (k) return k; }
  return null;
}

/** A class's icon as an element (a span holding the SVG), or null when it has none. */
export function classIcon(cls: string | null | undefined, size = 16): HTMLSpanElement | null {
  const key = classIconKey(cls);
  return key ? iconElement(key, size) : null;
}

export function iconElement(key: string, size = 16): HTMLSpanElement {
  const span = document.createElement('span');
  span.className = 'cls-icon-wrap';
  span.innerHTML = iconSvg(key, size);
  return span;
}
