/**
 * Enforce Drive's "viewers cannot download, print, or copy" on every pricing PDF.
 *
 * WHY THIS EXISTS
 * ---------------
 * `copyRequiresWriterPermission` is a PER-FILE property. It is NOT a folder setting and it
 * is NOT inherited. Applying it to a folder does nothing to the files inside, and nothing
 * applies it to files added later. So a one-off sweep protects exactly the files that
 * existed the moment it ran, and every PDF added since is unprotected.
 *
 * Measured 2026-08-14 on a 9-file sample of the live corpus: 4 of the 5 NEWEST files
 * (Xano table 10 ids 14222-14226, added 2026-08-06) downloaded anonymously as real PDF
 * bytes, versus 1 of 4 older files. That one older gap matters too - it is the signature
 * of a sweep that hit the execution cap partway and left a silent tail.
 *
 * WHAT IT DOES AND DOES NOT BUY YOU
 * ---------------------------------
 * This blocks SAVING A COPY. It does not block READING: a restricted file is still fully
 * readable in the Drive preview by anyone holding the link, and for a pricing product
 * reading the numbers IS the product. Treat this as anti-scraping / anti-redistribution.
 * The actual paywall is whatever decides who gets handed the link - that is Xano ep93,
 * fixed separately.
 *
 * THE 6-MINUTE CAP IS THE WHOLE DESIGN CONSTRAINT
 * -----------------------------------------------
 * Apps Script kills a run at ~6 minutes. With ~14k files a naive
 * `while (files.hasNext())` loop dies partway through and leaves the rest unprotected with
 * no error - which is almost certainly how the current gaps happened. This script is
 * therefore RESUMABLE: it persists a folder queue plus a page token in ScriptProperties,
 * stops itself before the cap, and picks up exactly where it left off on the next run.
 * Re-running is free (already-set files are skipped), so a frequent trigger is safe.
 *
 * SETUP
 * -----
 *   1. Set FOLDER_ID below to the root folder holding the PDFs.
 *   2. Services (+) -> Drive API -> add it, and SELECT VERSION **v3**.
 *   3. Run `auditOnly()` first. It writes nothing and tells you the real coverage number.
 *   4. Run `installTrigger()` once to schedule the sweep hourly.
 *
 * !! THE MOST LIKELY WAY THIS FAILS IS SILENTLY, ON THE WRONG API VERSION. !!
 * This file reads `page.files`, which is the Drive **v3** shape. The v2 advanced service
 * returns `page.items` instead, so on v2 every page looks empty: the script walks the tree,
 * finds nothing, protects nothing, and reports a clean "PASS COMPLETE - scanned 0". If the
 * first audit says it scanned 0 files, you are on v2, not looking at an empty folder.
 * `assertDriveV3_()` below turns that into a loud error instead of a quiet lie.
 *
 * Entry points: auditOnly() | sweep() | installTrigger() | resetState() | showState()
 */

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

/** Root folder containing the pricing PDFs. Subfolders are walked automatically. */
var FOLDER_ID = 'PUT_THE_FOLDER_ID_HERE';

/** Stop this far before the 6-minute cap so state is always saved cleanly. */
var MAX_RUN_MS = 4.5 * 60 * 1000;

/** Files per Drive list call. 100 keeps each call fast and the cursor fine-grained. */
var PAGE_SIZE = 100;

/** ScriptProperties key holding the resumable cursor. */
var STATE_KEY = 'DL_RESTRICT_STATE_V1';

/**
 * Only touch PDFs. Set to null to cover every non-folder file in the tree.
 * Left on by default so the script cannot alter unrelated assets (images, sheets).
 */
var MIME_FILTER = 'application/pdf';

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

/** Count unprotected files and log their ids. Writes nothing. Run this first. */
function auditOnly() {
  return run_(true);
}

/** Protect everything still unprotected, resuming across runs. Safe to re-run. */
function sweep() {
  return run_(false);
}

/** Schedule the sweep hourly. Removes any previously installed copy first. */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'sweep') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('sweep').timeBased().everyHours(1).create();
  Logger.log('Installed hourly trigger for sweep().');
}

/** Forget the cursor so the next run starts from the top of the tree. */
function resetState() {
  PropertiesService.getScriptProperties().deleteProperty(STATE_KEY);
  Logger.log('State cleared - next run starts a fresh pass.');
}

/** Inspect progress without changing anything. */
function showState() {
  var raw = PropertiesService.getScriptProperties().getProperty(STATE_KEY);
  Logger.log(raw ? raw : 'No state - next run starts a fresh pass.');
  return raw;
}

// ---------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------

/**
 * Fail loudly if the advanced service is v2, rather than reporting a clean sweep over
 * zero files. v2 returns `items`; v3 returns `files`.
 *
 * THE PROBE MUST NOT USE ANY VERSION-SPECIFIC PARAMETER. An earlier version of this
 * function probed with `fields: 'files(id)'` and `pageSize`, which are v3-only: on a v2
 * service Drive rejects the call outright with
 *   "GoogleJsonResponseException: API call to drive.files.list failed with error:
 *    Invalid field selection files"
 * so the guard exploded with a raw API error instead of the readable message it exists to
 * produce. `q` alone is understood by both versions, so the shape of the RESULT is what
 * identifies the version.
 */
function assertDriveV3_() {
  var probe;
  try {
    probe = Drive.Files.list({ q: "'" + FOLDER_ID + "' in parents and trashed = false" });
  } catch (e) {
    var msg = String(e && e.message ? e.message : e);
    // Belt and braces: if a v2 service still objects to something here, name the cause
    // rather than surfacing the raw exception.
    if (msg.indexOf('Invalid field selection') !== -1) {
      throw new Error(
        'Drive advanced service is v2, but this script needs v3. ' +
        'In the editor: Services -> Drive API -> Version -> v3 -> Save, then re-run. ' +
        '(Original error: ' + msg + ')'
      );
    }
    throw new Error(
      'Drive.Files.list failed for FOLDER_ID "' + FOLDER_ID + '". Check the id is a ' +
      'folder you can open and that the Drive API service is enabled. (' + msg + ')'
    );
  }

  if (probe && probe.files) return;                 // v3, as required

  if (probe && probe.items) {
    throw new Error(
      'Drive advanced service is v2 (returned "items"). This script needs v3. ' +
      'Services -> Drive API -> Version -> v3 -> Save, then re-run.'
    );
  }
  // Neither shape: almost always a bad FOLDER_ID or no access to it.
  throw new Error(
    'Drive returned no recognisable file list for FOLDER_ID "' + FOLDER_ID + '". ' +
    'Check the id is a folder you can open, and that the Drive API service is enabled.'
  );
}

/**
 * Set the restriction on one file.
 *
 * The 4-argument (resource, fileId, media, optionalArgs) form is what carries
 * supportsAllDrives, which a Shared Drive requires. Some advanced-service builds reject
 * that shape for a metadata-only update, where the documented 2-argument form is correct.
 * Rather than guess which one this project has - and this script cannot be dry-run from
 * outside Apps Script - try the explicit form and fall back. A genuine permission error
 * throws from both and is handled by the caller.
 */
function setRestricted_(fileId) {
  var resource = { copyRequiresWriterPermission: true };
  try {
    Drive.Files.update(resource, fileId, null, { supportsAllDrives: true });
  } catch (e) {
    Drive.Files.update(resource, fileId);
  }
}

function run_(auditMode) {
  if (!FOLDER_ID || FOLDER_ID === 'PUT_THE_FOLDER_ID_HERE') {
    throw new Error('Set FOLDER_ID before running.');
  }
  assertDriveV3_();

  var started = Date.now();
  var props = PropertiesService.getScriptProperties();

  // An audit must always measure the WHOLE tree, so it never resumes a partial sweep.
  var state = auditMode ? null : readState_(props);
  if (!state) {
    state = { queue: [FOLDER_ID], pageToken: null, scanned: 0, fixed: 0, failed: 0, passStarted: started };
  }

  var unprotectedIds = [];

  while (state.queue.length > 0) {
    if (Date.now() - started > MAX_RUN_MS) {
      if (!auditMode) writeState_(props, state);
      Logger.log(summary_(state, auditMode, false));
      return state;
    }

    var folderId = state.queue[0];
    var page;

    try {
      page = Drive.Files.list({
        q: "'" + folderId + "' in parents and trashed = false",
        fields: 'nextPageToken, files(id, name, mimeType, copyRequiresWriterPermission)',
        pageToken: state.pageToken || undefined,
        pageSize: PAGE_SIZE,
        supportsAllDrives: true,
        includeItemsFromAllDrives: true
      });
    } catch (e) {
      // A transient Drive error should not lose the cursor - save and let the next run retry.
      Logger.log('LIST FAILED on folder ' + folderId + ': ' + e);
      if (!auditMode) writeState_(props, state);
      throw e;
    }

    var files = page.files || [];
    for (var i = 0; i < files.length; i++) {
      var f = files[i];

      if (f.mimeType === 'application/vnd.google-apps.folder') {
        state.queue.push(f.id);
        continue;
      }
      if (MIME_FILTER && f.mimeType !== MIME_FILTER) continue;

      state.scanned++;

      // Already restricted - skip. This is what makes re-runs cheap and idempotent.
      if (f.copyRequiresWriterPermission === true) continue;

      unprotectedIds.push(f.id);
      if (auditMode) continue;

      try {
        setRestricted_(f.id);
        state.fixed++;
      } catch (e) {
        // Most likely cause: the executing account is not owner/writer on that file.
        // Keep going - one bad file must not abort the pass.
        state.failed++;
        Logger.log('UPDATE FAILED ' + f.id + ' (' + f.name + '): ' + e);
      }
    }

    if (page.nextPageToken) {
      state.pageToken = page.nextPageToken;
    } else {
      state.queue.shift();
      state.pageToken = null;
    }
  }

  // Tree exhausted - drop the cursor so the next run re-verifies from the top.
  if (!auditMode) props.deleteProperty(STATE_KEY);

  Logger.log(summary_(state, auditMode, true));
  if (unprotectedIds.length > 0) {
    Logger.log(
      (auditMode ? 'UNPROTECTED (' : 'WAS UNPROTECTED (') + unprotectedIds.length + '): ' +
      unprotectedIds.slice(0, 200).join(', ') +
      (unprotectedIds.length > 200 ? ' ...(' + (unprotectedIds.length - 200) + ' more)' : '')
    );
  }
  return { scanned: state.scanned, fixed: state.fixed, failed: state.failed, unprotected: unprotectedIds.length };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readState_(props) {
  var raw = props.getProperty(STATE_KEY);
  if (!raw) return null;
  try {
    var s = JSON.parse(raw);
    return (s && s.queue) ? s : null;   // ignore anything malformed rather than crash
  } catch (e) {
    return null;
  }
}

function writeState_(props, state) {
  props.setProperty(STATE_KEY, JSON.stringify(state));
}

function summary_(state, auditMode, complete) {
  return (auditMode ? '[AUDIT] ' : '[SWEEP] ') +
    (complete ? 'PASS COMPLETE' : 'paused at time budget, will resume') +
    ' | scanned ' + state.scanned +
    ' | fixed ' + state.fixed +
    ' | failed ' + state.failed +
    ' | folders queued ' + state.queue.length;
}
