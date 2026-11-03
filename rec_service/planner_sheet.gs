/**
 * Tulle Planner activity log - receiver for rec_service/sheet_log.py (owner 2026-11-03).
 *
 * SETUP (once, ~5 minutes, in the Google Sheet the team will use):
 *   1. Extensions > Apps Script. Delete what's there, paste this whole file, Save.
 *   2. Project Settings (gear) > Script Properties > Add property:  SECRET = <a long random string>
 *      (the same value goes on Railway rec-engine as REC_SHEET_SECRET).
 *   3. Run > setup  (once; approve the permission prompt). It makes the "Planner log" tab, header and dropdown.
 *   4. Deploy > New deployment > type "Web app": Execute as = Me, Who has access = Anyone. Deploy, copy the URL
 *      ending in /exec -> Railway rec-engine variable REC_SHEET_WEBHOOK_URL.
 *   "Anyone" only means the URL can be called; without the SECRET a call is refused and writes nothing.
 *   Share the Sheet itself with named teammates only (it holds couples' emails and conversations).
 *
 * UPDATING (e.g. 2026-11-03 Device + Browser): paste the new file, Save, then Deploy > Manage deployments > pencil >
 *   Version: New version > Deploy. The /exec URL stays the same, so nothing changes on Railway.
 *
 * Columns A:K come from the server, L:M (Category, Notes) are the team's, N:O (Device, Browser) come from the server
 * again - placed after Notes (2026-11-03) so no column the team already fills had to move.
 */
var TAB = 'Planner log';
var HEADER = ['Time (ET)', 'User', 'User id', 'Plan at the time', 'Chat #', 'Action', 'What they did or said',
              "Tulle's answer", 'Vendors shown', 'Filters used', 'Flag', 'Category', 'Notes', 'Device', 'Browser'];
var SERVER_COLS = 11;      // A:K
var EXTRA_COL = 14;        // N: first of the server columns after the team's Category + Notes
var EXTRA_COLS = 2;        // N:O Device, Browser
var CATEGORIES = ['Venue search', 'Specific venue', 'Other vendor type', 'Pricing / cost question', 'Budget / planning',
                  'Profile update', 'Feedback', 'Missing venue / data gap', 'Bug / bad answer', 'Other'];

function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(TAB) || ss.insertSheet(TAB, 0);
  if (sh.getLastRow() === 0) sh.appendRow(HEADER);
  sh.setFrozenRows(1);
  sh.getRange(1, 1, 1, HEADER.length).setFontWeight('bold').setBackground('#EDF9F3');
  ensureHeader(sh);
  var widths = [130, 190, 70, 90, 60, 170, 300, 420, 240, 240, 140, 160, 220, 120, 160];
  for (var i = 0; i < widths.length; i++) sh.setColumnWidth(i + 1, widths[i]);
  sh.getRange('G:H').setWrap(true);
  var rule = SpreadsheetApp.newDataValidation().requireValueInList(CATEGORIES, true).setAllowInvalid(true).build();
  sh.getRange(2, 12, sh.getMaxRows() - 1, 1).setDataValidation(rule);
  if (!sh.getFilter()) sh.getRange(1, 1, sh.getMaxRows(), HEADER.length).createFilter();
}

// Writes any header cell that's missing (a sheet made before a column was added gets it on the next post).
function ensureHeader(sh) {
  var have = sh.getRange(1, 1, 1, HEADER.length).getValues()[0];
  for (var i = 0; i < HEADER.length; i++) {
    if (have[i] === '' || have[i] === null) sh.getRange(1, i + 1).setValue(HEADER[i]).setFontWeight('bold').setBackground('#EDF9F3');
  }
}

function clean(v) {        // never let a cell start a formula
  var s = v === null || v === undefined ? '' : v;
  return (typeof s === 'string' && /^[=+\-@]/.test(s)) ? "'" + s : s;
}

function doPost(e) {
  var out = function (o) { return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON); };
  var body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return out({ ok: false, error: 'bad json' }); }
  var secret = PropertiesService.getScriptProperties().getProperty('SECRET');
  if (!secret || body.secret !== secret) return out({ ok: false, error: 'bad secret' });
  var rows = (body.rows || []).filter(function (r) { return Array.isArray(r); }).map(function (r) {
    var row = r.slice(0, SERVER_COLS + EXTRA_COLS);
    while (row.length < SERVER_COLS + EXTRA_COLS) row.push('');   // an older server sends 11: N:O stay blank
    return row.map(clean);
  });
  if (!rows.length) return out({ ok: true, added: 0 });
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(TAB);
    if (!sh) { setup(); sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(TAB); }
    ensureHeader(sh);
    var at = sh.getLastRow() + 1;
    sh.getRange(at, 1, rows.length, SERVER_COLS).setValues(rows.map(function (r) { return r.slice(0, SERVER_COLS); }));
    sh.getRange(at, EXTRA_COL, rows.length, EXTRA_COLS).setValues(rows.map(function (r) { return r.slice(SERVER_COLS); }));
  } finally {
    lock.releaseLock();
  }
  return out({ ok: true, added: rows.length });
}
