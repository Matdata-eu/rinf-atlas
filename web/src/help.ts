// "Help" dialog: a short user guide, one foldable section per feature. Controls are named by
// their label, not by where they sit on screen, so the guide survives layout changes.
import { h } from './state';

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
        section('Getting started', true,
          ['Pick ', b('PROD'), ' or ', b('UAT'), ' in the environment list next to the logo.'],
          ['Type in the ', b('search'), ' box to match labels and codes (operational point names, line numbers, IDs).'],
          ['Click anything on the map to see its details. Pan and zoom as usual; small elements such as signals ',
            'or switches only appear when you zoom in.']),
        section('Filters',  false,
          [b('Classes'), ': tick the kinds of infrastructure to show. The number is how many match the current filters.'],
          ['The filter button next to a class opens filters on that class’s own properties.'],
          [b('Filters for every class'), ' (country, infrastructure manager…) apply to everything. Values with 0 matches are greyed out.'],
          ['Active filters appear as chips at the top; click a chip’s × to remove it.'],
          [b('Limit to map view'), ' restricts counts and results to what is on screen.']),
        section('Map colours', false,
          ['Under ', b('Map colours'), ', choose a property (for example maximum speed or energy supply) to colour the map by its value.'],
          ['In the legend, click an entry to hide it, ', k('Shift'), '+click to show only that one. × goes back to class colours.']),
        section('Results and export', false,
          ['The ', b('Results'), ' tab lists matching elements; click one to open it, or its zoom icon to fly to it.'],
          [b('Zoom to results'), ' fits the map to everything that matches.'],
          ['Export as ', b('CSV'), ', ', b('Excel'), ' (one sheet per class) or ', b('Turtle'), ' (live from ERA). ',
            b('Open in GIS'), ' copies a URL to load the results as layers in QGIS or ArcGIS.']),
        section('Element details', false,
          [b('Properties'), ': the indexed values. Links open the related element.'],
          [b('Describe'), ': every triple about the element, fetched live from ERA. Click an IRI to follow it; use ',
            b('Back'), ' and ', b('Forward'), ' (or ', k('Alt'), '+', k('←'), ' / ', k('→'), ') to retrace your steps.'],
          [b('Graph'), ': the same data as a network. Double-click a node to expand it.'],
          [k('Ctrl'), '+click (', k('⌘'), '+click on a Mac) on the map opens an element straight in Describe.']),
        section('Line profile', false,
          ['Open ', b('Line profile'), ' and choose ', b('National line'), ', ', b('Sections'), ' (click sections of line on the map) or ',
            b('From → to'), ' (shortest route between two operational points).'],
          ['Each row shows one track parameter along the distance. Hover a cell to highlight it on the map, click it for details.'],
          ['Choose which rows to show, and download the strip as CSV, with the buttons in the profile’s header.']),
        section('Vehicle check', false,
          ['Open ', b('Vehicle check'), ' and pick or create a vehicle profile (gauge, energy supply, axle load…).'],
          ['Sections of line turn ', b('green'), ' when at least one track fits every characteristic you filled in, ',
            b('yellow'), ' when some could not be checked for lack of data, and ', b('red'), ' when no track fits.'],
          ['Profiles are stored in this browser; export and import them as JSON or Turtle. ',
            'This is an indication from published data, not an official route compatibility check.']),
        section('Datasets and data quality', false,
          ['The ', b('datasets'), ' status button shows each infrastructure manager’s dataset: when it was published and processed, and whether it loaded.'],
          [b('Show on map'), ' limits the map to one dataset. New datasets are picked up automatically.'],
          ['The ', b('Data issues'), ' layer rings elements where ERA’s validation rules found problems. An element’s details list them.']),
        section('Sharing and display', false,
          [b('Share'), ' copies a link to exactly this view: map position, filters, selected element and open tools.'],
          ['The ', b('OpenRailwayMap'), ' layer adds a railway overlay to the base map.'],
          ['Whoever runs this site may also have added their own map layers next to it.'],
          ['The theme button switches between system, light and dark.'],
          ['On a phone, drag the panel’s handle up or down to see more of the filters or the map.'])));
    document.body.append(dlg);
  }
  dlg.showModal();
}
