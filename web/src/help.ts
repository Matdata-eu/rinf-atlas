// "Help" dialog: a short user guide, one foldable section per feature. Controls are named by
// their label, not by where they sit on screen, so the guide survives layout changes.
import { COMPAT_DISCLAIMER, COMPAT_DISCLAIMER_TITLE } from './disclaimer';
import { openShapes } from './shapes';
import { h } from './state';
import { whatsNewLink } from './whats-new';
import { openTour } from './tour';

let dlg: HTMLDialogElement | null = null;

const b = (text: string) => h('strong', {}, text);
const k = (text: string) => h('kbd', {}, text);

type Part = string | Node;
const section = (title: string, open: boolean, ...items: Part[][]) =>
  h('details', { class: 'help-sec', open: open || undefined },
    h('summary', {}, title),
    h('ul', {}, ...items.map((parts) => h('li', {}, ...parts))));

export function openHelp() {
  if (!dlg) {
    dlg = h('dialog', { class: 'datasets-dialog about-dialog help-dialog', 'aria-label': 'Help' });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); });
    dlg.append(
      h('div', { class: 'dlg-head' },
        h('h2', {}, 'How to use RINF Atlas'),
        h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg?.close() }, '×')),
      h('div', { class: 'about-body help-body' },
        h('p', { class: 'muted' }, 'Find railway infrastructure from the EU Register of Infrastructure (RINF) on the map, ',
          'narrow it down with filters, and inspect any element. Open a topic below.'),
        h('p', { class: 'muted' }, 'Recent changes: ', whatsNewLink(() => dlg), '.'),
        h('p', { class: 'help-tour' }, 'New here? Take the short tour: ',
          h('button', { class: 'link', type: 'button', onclick: () => { dlg?.close(); openTour(); } }, 'How RINF Atlas works'), '.'),
        section('Getting started', true,
          ['Pick ', b('PROD'), ' or ', b('UAT'), ' in the environment list next to the logo.'],
          ['Type in the ', b('search'), ' box to match labels and codes (operational point names, line numbers, IDs).'],
          ['Click anything on the map to see its details. Pan and zoom as usual; small elements such as signals ',
            'or switches only appear when you zoom in (the current zoom shows above the scale bar, bottom left). ',
            'Click ', b('zoom in to see'), ' next to a class to draw it at every zoom, e.g. for a coloured map of the whole network.']),
        section('Filters',  false,
          [b('Classes'), ': tick the kinds of infrastructure to show. The number is how many match the current filters.'],
          ['The filter button next to a class opens filters on that class’s own properties.'],
          [b('Filters for every class'), ' (country, infrastructure manager…) apply to everything. Values with 0 matches are greyed out.'],
          ['Active filters appear as chips at the top; click a chip’s × to remove it.'],
          [b('Limit to map view'), ' restricts counts and results to what is on screen.']),
        section('Views', false,
          ['A ', b('View'), ' at the top of the filters opens a ready-made lens, such as the European transport corridors: ',
            'its classes, filters and map colours, with its parameters listed first.'],
          ['Pick a corridor or country to zoom to it. ', b('Show every class and filter'), ' brings the other filters back.'],
          ['Some views check requirements, for example those of the TEN-T Regulation. Each track gets ', b('Meets the requirement'), ', ',
            b('Data missing'), ' (RINF does not say) or ', b('Does not meet the requirement'), '; a section of line meets one when one of its tracks does.'],
          [b('Requirements table'), ' sums the checks per corridor or country, in kilometres or numbers, and downloads as CSV. ',
            'It shows what RINF declares, not legal compliance: exemptions are not taken into account.']),
        section('Map colours', false,
          ['Under ', b('Map colours'), ', choose a property (for example maximum speed or energy supply) to colour the map by its value.'],
          ['In the legend, click an entry to hide it, ', k('Shift'), '+click to show only that one. × goes back to class colours.']),
        section('Results and export', false,
          ['The ', b('Results'), ' tab lists matching elements; click one to open it, or its zoom icon to fly to it.'],
          [b('Zoom to results'), ' fits the map to everything that matches.'],
          [b('Export…'), ' downloads the matching elements. The quick export gives the facet columns as CSV or Excel at once. ',
            'A full export is prepared in the background from the local copy of the ERA data and holds every RINF property: ',
            'as RDF (Turtle, N-Triples, JSON-LD, with components such as net references, geometries and contact line systems resolved), ',
            'as a table (CSV, Excel; columns from the Full RINF profile with the RINF parameter numbers, your own choice of columns, or one row per value) ',
            'or as a GIS file (GeoJSON, GeoPackage). Each export comes with a recipe describing how it was made.'],
          [b('Open in GIS'), ' copies a URL to load the results as layers in QGIS or ArcGIS.']),
        section('Element details', false,
          [b('Properties'), ': the indexed values. Links open the related element.'],
          [b('Describe'), ': every triple about the element, fetched live from ERA. Click an IRI to follow it; use ',
            b('Back'), ' and ', b('Forward'), ' (or ', k('Alt'), '+', k('←'), ' / ', k('→'), ') to retrace your steps.'],
          [b('Graph'), ': the same data as a network. Double-click a node to expand it.'],
          [k('Ctrl'), '+click (', k('⌘'), '+click on a Mac) on the map opens an element straight in Describe.']),
        section('Element position', false,
          ['Many elements have no coordinates of their own in RINF, for example tracks, platform edges, signals and switches. ',
            'They are drawn at the position of the operational point or section of line they belong to, so several of them can sit on the same spot.'],
          ['A section of line without a geometry is drawn as a straight line between its start and end operational points, not along the real track.'],
          ['Elements whose position cannot be worked out either are listed in the results but not drawn on the map.'],
          ['An element’s details say under ', b('Position'), ' whether it has its own coordinates or which elements its position was taken from.'],
          ['Exactly how a position is inferred is not fixed by RINF: whoever runs this site defines it in the SHACL ',
            h('button', { class: 'link', type: 'button', onclick: () => { dlg?.close(); openShapes(); } }, 'facet shapes'),
            ' it is configured with (', h('code', {}, 'atlas:geometryPath'), ', ', h('code', {}, 'atlas:geometryFrom'), ' and ',
            h('code', {}, 'atlas:lineBetween'), '). The same shapes decide the classes and filters.']),
        section('Line profile', false,
          ['Open ', b('Line profile'), ' and choose ', b('National line'), ', ', b('Sections'), ' (click sections of line on the map) or ',
            b('From → to'), ' (shortest route between two operational points).'],
          ['Each row shows one track parameter along the distance. Hover a cell to highlight it on the map, click it for details.'],
          ['Choose which rows to show, and download the strip as CSV, with the buttons in the profile’s header.']),
        section('Vehicle check', false,
          ['Open ', b('Vehicle check'), ' and pick or create a vehicle profile (gauge, energy supply, axle load…).'],
          ['Sections of line turn ', b('green'), ' when at least one track fits every characteristic you filled in, ',
            b('yellow'), ' when some could not be checked for lack of data, and ', b('red'), ' when no track fits.'],
          ['Profiles are stored in this browser; export and import them as JSON or Turtle.'],
          ['The first time, you have to accept the disclaimer below; this browser remembers it, and the ',
            'disclaimer stays available, folded, at the top of the panel.'],
          [b(`${COMPAT_DISCLAIMER_TITLE}. `), COMPAT_DISCLAIMER]),
        section('Datasets and data quality', false,
          ['The ', b('datasets'), ' status button shows each infrastructure manager’s dataset: when it was published and processed, and whether it loaded.'],
          [b('Show on map'), ' limits the map to one dataset. New datasets are picked up automatically.'],
          ['The ', b('Data issues'), ' map layer rings elements where ERA’s validation rules found problems. When a click lands on several elements, the list to choose from marks the ones with problems too. An element’s details list them.']),
        section('Sharing and display', false,
          [b('Share'), ' copies a link to exactly this view: map position, filters, selected element and open tools.'],
          ['The ', b('Map layers'), ' button by the zoom buttons opens a menu of overlays: ', b('OpenRailwayMap'),
            ' adds a railway overlay to the base map, and ', b('Data issues'), ' rings elements with problems.'],
          ['Whoever runs this site may also have added their own layers to that menu, such as aerial photos or a company map.'],
          ['The theme button switches between system, light and dark.'],
          ['On a phone, drag the panel’s handle up or down to see more of the filters or the map.'])));
    document.body.append(dlg);
  }
  dlg.showModal();
}
