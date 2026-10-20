#!/usr/bin/env node
/*
 * BookKnowledge read-only index builder.
 * Usage:
 *   BOOKKNOWLEDGE_DIR=/path/to/BookKnowledge node build_bookknowledge_index.js
 *
 * Reads only; writes BOOKKNOWLEDGE_INDEX.json and AUDIT_SUMMARY.md next to this script.
 * No absolute paths are written into the output.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = process.env.BOOKKNOWLEDGE_DIR;
if (!ROOT) {
  console.error('ERROR: set BOOKKNOWLEDGE_DIR to the BookKnowledge root (read-only input).');
  process.exit(2);
}
const OUT_DIR = __dirname;
const A_DIR = path.join(ROOT, 'A_已完成_可调用技能');
const INDEX_CSV = path.join(A_DIR, 'index.csv');
const T_REGISTRY = path.join(ROOT, 'F_辅助产出', 'T注册表.md');
const CROSS_INDEX = path.join(ROOT, 'F_辅助产出', '跨书信号索引.md');
const T_TOKEN = /T-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*/g;
const T_ID_CANON = /^T-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+$/;

const sha256 = (data) => crypto.createHash('sha256').update(data).digest('hex');
const rel = (p) => path.relative(ROOT, p).split(path.sep).join('/');
const read = (p) => fs.readFileSync(p, 'utf8');
const existsFile = (p) => { try { return fs.statSync(p).isFile(); } catch { return false; } };
const existsDir = (p) => { try { return fs.statSync(p).isDirectory(); } catch { return false; } };
const listFiles = (p) => { try { return fs.readdirSync(p, { withFileTypes: true }).filter(d => d.isFile()).map(d => d.name); } catch { return []; } };

function parseCSV(text) {
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      } else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else if (c !== '\r') field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}

function normTitle(s) {
  return String(s || '')
    .replace(/[（(][^）)]*[）)]/g, ' ')
    .replace(/[\[【][^\]】]*[\]】]/g, ' ')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/\b(2nd|3rd|4th|5th|6th|second|third|fourth|fifth|sixth)\s*(ed|edition)\b/g, ' ')
    .replace(/\b(edition|ed\.)\b/g, ' ')
    .replace(/第\s*\d+\s*版/g, ' ')
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function stableId(prefix, text, len = 10) {
  return prefix + '-' + sha256(text).slice(0, len);
}

function unique(arr) { return [...new Set(arr)]; }
function countBy(arr) {
  const out = {};
  for (const x of arr) out[x] = (out[x] || 0) + 1;
  return out;
}

// ---------- index.csv ----------
const indexRaw = read(INDEX_CSV);
const indexRowsRaw = parseCSV(indexRaw.replace(/^\uFEFF/, ''));
const indexHeader = indexRowsRaw[0].map(s => s.trim());
const indexRows = indexRowsRaw.slice(1)
  .filter(r => r.length > 1 && r[0].trim())
  .map((r, i) => {
    const o = {};
    indexHeader.forEach((h, j) => { o[h] = (r[j] || '').trim(); });
    o._row = i + 2;
    return o;
  });

// ---------- directories ----------
const dirNames = fs.readdirSync(A_DIR, { withFileTypes: true })
  .filter(d => d.isDirectory())
  .map(d => d.name)
  .sort((a, b) => a.localeCompare(b));

const isNonbook = (name) => /^_/.test(name) || name === 'SKILL文件' || name === 'SKILL文件/';
const nonbookReason = (name) => {
  if (name === 'SKILL文件') return 'collection_dir';
  if (name === '_bk_scratch') return 'scratch_dir';
  if (/^_孟洪涛/.test(name)) return 'nonbook_research_dir';
  if (/^_/.test(name)) return 'underscore_prefix';
  return null;
};

const CANON = /^Phase([0-5])_(元信息与定位|核心框架|关键信号与可执行判据|深度评估_跟书吵架|实践适配|验证计划与整体评估)\.md$/;
const STARTS = /^Phase([0-5])/;
const PHASE_NAME = /Phase([0-5])/;

// ---------- T registry ----------
const tRegistryRaw = read(T_REGISTRY).replace(/^\uFEFF/, '');
const crossIndexRaw = read(CROSS_INDEX).replace(/^\uFEFF/, '');
const tRegistryLines = tRegistryRaw.split(/\r?\n/);
const tRows = [];
for (let i = 0; i < tRegistryLines.length; i++) {
  const line = tRegistryLines[i];
  if (!/^\|\s*T-/.test(line)) continue;
  const cells = line.split('|').slice(1, -1).map(s => s.trim());
  const tId = cells[0] || '';
  const schema = cells.length === 8 ? 'v1_8col' : cells.length === 12 ? 'v2_12col' : 'other_' + cells.length;
  const validId = T_ID_CANON.test(tId);
  const record = {
    line: i + 1,
    t_id: tId,
    valid_t_id: validId,
    schema_shape: schema,
    cells_n: cells.length,
    raw_cells: cells,
    book: cells[1] || '',
    field_3: cells[2] || '',
    field_4: cells[3] || '',
    field_5: cells[4] || '',
    last_nonempty: [...cells].reverse().find(Boolean) || '',
    signature: sha256(JSON.stringify(cells.slice(1))),
    field_mapping_confidence: schema === 'v1_8col' ? 'high' : 'low',
  };
  tRows.push(record);
}

const tById = new Map();
for (const r of tRows) {
  if (!tById.has(r.t_id)) tById.set(r.t_id, []);
  tById.get(r.t_id).push(r);
}
const tIdCounts = [...tById.values()].map(a => a.length);
const tCountDist = countBy(tIdCounts);
const tCollisions = [...tById.entries()].filter(([, a]) => a.length > 1);
const tExactDuplicates = tCollisions.filter(([, a]) => new Set(a.map(r => r.signature)).size === 1);
const tAmbiguous = tCollisions.filter(([, a]) => new Set(a.map(r => r.signature)).size > 1);
const tRegistryIds = new Set(tById.keys());
const tSchemaShapes = countBy(tRows.map(r => r.schema_shape));
const tParseFailures = tRows.filter(r => !r.valid_t_id || !r.book);
const tFieldMappingAmbiguous = tRows.filter(r => r.field_mapping_confidence === 'low');
const registryRowsWithDependsOn = tRegistryLines.filter(l => /depends_on/i.test(l)).length;
const registryRowsWithConflictsWith = tRegistryLines.filter(l => /conflicts_with/i.test(l)).length;

// ---------- records ----------
const registryBookExact = new Map();
const registryBookNorm = new Map();
for (const r of tRows) {
  if (!r.book) continue;
  if (!registryBookExact.has(r.book)) registryBookExact.set(r.book, []);
  registryBookExact.get(r.book).push(r.t_id);
  const n = normTitle(r.book);
  if (!registryBookNorm.has(n)) registryBookNorm.set(n, []);
  registryBookNorm.get(n).push(r.t_id);
}

let phaseFilesWithDependsOn = 0;
let phaseFilesWithConflictsWith = 0;

const records = [];
for (const name of dirNames) {
  const dir = path.join(A_DIR, name);
  const phaseDir = path.join(dir, '拆解记录');
  const indexRow = indexRows.find(r => r.name === name) || null;
  const phaseFiles = listFiles(phaseDir).filter(f => f.endsWith('.md') && f.includes('Phase')).sort();
  const canonicalFiles = [];
  const variantFiles = [];
  const legacyFiles = [];
  const slots = new Set();
  const canonicalSlots = new Set();
  const qualityValues = [];
  const qualityFiles = [];
  const bookTypes = [];
  const statuses = [];
  const phaseStatusFormats = [];
  const phaseTIds = new Set();
  const nonstandardTokens = new Set();
  const phaseFileList = [];
  let parseFailures = [];

  for (const f of phaseFiles) {
    const full = path.join(phaseDir, f);
    phaseFileList.push(rel(full));
    const content = read(full);
    const starts = f.match(STARTS);
    const canonical = f.match(CANON);
    if (starts) {
      slots.add(Number(starts[1]));
      if (canonical) { canonicalFiles.push(f); canonicalSlots.add(Number(canonical[1])); }
      else variantFiles.push(f);
    } else {
      legacyFiles.push(f);
    }

    const qMatches = [...content.matchAll(/^\s*quality_level\s*:\s*([^\n\r#]+)/gm)].map(m => m[1].trim());
    const bMatches = [...content.matchAll(/^\s*book_type\s*:\s*([^\n\r#]+)/gm)].map(m => m[1].trim());
    const sMatches = [...content.matchAll(/^\s*status\s*:\s*([^\n\r#]+)/gm)].map(m => m[1].trim());
    if (qMatches.length) {
      qualityFiles.push(f);
      qualityValues.push(...qMatches);
      if (/<!--\s*phase_status:/.test(content)) phaseStatusFormats.push('comment');
      else if (/```ya?ml[\s\S]*?quality_level\s*:/.test(content)) phaseStatusFormats.push('yaml');
      else phaseStatusFormats.push('loose');
    } else if (/phase_status|quality_level/.test(content)) {
      phaseStatusFormats.push('unparsed');
      parseFailures.push(f + ': quality_level present but unparsed');
    }
    bookTypes.push(...bMatches);
    statuses.push(...sMatches);
    if (/^\s*depends_on\s*:/m.test(content)) phaseFilesWithDependsOn++;
    if (/^\s*conflicts_with\s*:/m.test(content)) phaseFilesWithConflictsWith++;

    for (const m of content.matchAll(T_TOKEN)) {
      if (T_ID_CANON.test(m[0])) phaseTIds.add(m[0]);
      else nonstandardTokens.add(m[0]);
    }
  }

  const qualityUnique = unique(qualityValues.map(q => q.replace(/[。；;].*$/, '').trim()));
  let declaredQuality = null;
  if (qualityUnique.length === 1) {
    if (qualityUnique[0] === '精读级' || qualityUnique[0] === '框架级') declaredQuality = qualityUnique[0];
    else if (/待定|待精读/.test(qualityUnique[0])) declaredQuality = 'pending';
    else declaredQuality = qualityUnique[0];
  } else if (qualityUnique.length > 1) declaredQuality = 'mixed';

  const bookTypeUnique = unique(bookTypes.map(b => b.replace(/#.*$/, '').trim()).filter(Boolean));
  const statusUnique = unique(statuses.map(s => s.trim()).filter(Boolean));

  const sourceDir = path.join(dir, '源文件');
  const fulltextDir = path.join(dir, '全书文本');
  const sourceFiles = listFiles(sourceDir);
  const fulltextFiles = listFiles(fulltextDir);
  const fulltextMain = existsFile(path.join(fulltextDir, '全书文字.md'));
  const fulltextAny = fulltextFiles.length > 0;
  const skeleton = existsFile(path.join(phaseDir, '目录骨架.md'));

  const registryTIds = unique([
    ...(registryBookExact.get(name) || []),
    ...(registryBookNorm.get(normTitle(name)) || []),
  ]);
  const collisionsForBook = registryTIds.filter(id => (tById.get(id) || []).length > 1);

  const nonbook = isNonbook(name);
  const flags = [];
  if (nonbook) flags.push('nonbook');
  if (!indexRow) flags.push('index_missing');
  if (!sourceFiles.length) flags.push('source_missing');
  if (!fulltextMain) flags.push('fulltext_main_missing');
  if (!fulltextAny) flags.push('fulltext_any_missing');
  if (!phaseFiles.length) flags.push('phase_missing');
  if (phaseFiles.length && !(slots.size === 6)) flags.push('phase_incomplete');
  if (phaseFiles.filter(f => STARTS.test(f)).length > slots.size) flags.push('phase_extra_files');
  if (legacyFiles.length) flags.push('legacy_phase_files');
  if (variantFiles.length) flags.push('phase_name_variants');
  if (!qualityFiles.length) flags.push('quality_unmarked');
  if (declaredQuality === 'mixed') flags.push('quality_mixed');
  if (!bookTypeUnique.length) flags.push('book_type_missing');
  if (collisionsForBook.length) flags.push('t_registry_collision');
  if (/Quality of Earnings|QRM/.test(name)) flags.push('known_empty_shell');
  flags.push('read_not_verified');

  let auditStatus = 'inventory_ok';
  if (nonbook) auditStatus = 'nonbook';
  else if (!fulltextAny && !sourceFiles.length) auditStatus = 'source_blocked';
  else if (!fulltextAny) auditStatus = 'needs_conversion';
  else if (phaseFiles.length && slots.size < 6) auditStatus = 'phase_incomplete';
  else if (/Quality of Earnings|QRM/.test(name)) auditStatus = 'needs_review';
  else if (!qualityFiles.length) auditStatus = 'quality_unmarked';
  else if (flags.includes('phase_extra_files') || flags.includes('legacy_phase_files')) auditStatus = 'inventory_with_extras';
  else if (declaredQuality === 'mixed') auditStatus = 'quality_mixed';
  else if (collisionsForBook.length) auditStatus = 't_registry_collision';

  records.push({
    record_id: stableId('bk', name),
    work_id: stableId('work', normTitle(name)),
    edition_id: stableId('edition', name),
    title: name,
    record_kind: nonbook ? 'nonbook' : 'book',
    nonbook_reason: nonbookReason(name),
    indexed: Boolean(indexRow),
    index_row: indexRow ? indexRow._row : null,
    domain: indexRow ? indexRow.domain : null,
    modules: indexRow ? indexRow.modules : null,
    completeness: indexRow ? indexRow.complete : null,
    evidence: indexRow ? indexRow.evidence : null,
    confidence: indexRow ? indexRow.conf : null,
    index_desc: indexRow ? indexRow.desc : null,
    path: rel(dir) + '/',
    presence: {
      source_dir: existsDir(sourceDir),
      source_files: sourceFiles.length,
      fulltext_dir: existsDir(fulltextDir),
      fulltext_files: fulltextFiles.length,
      fulltext_main: fulltextMain,
      fulltext_any: fulltextAny,
      phase_dir: existsDir(phaseDir),
      skeleton: skeleton,
      per_book_skill_md: existsFile(path.join(dir, 'SKILL.md')),
    },
    phase: {
      files_total: phaseFiles.length,
      file_list: phaseFileList,
      canonical_files: canonicalFiles.length,
      variant_files: variantFiles.length,
      legacy_files: legacyFiles.length,
      slots: [...slots].sort(),
      slot_count: slots.size,
      canonical_slots: [...canonicalSlots].sort(),
      extra_files: phaseFiles.filter(f => STARTS.test(f)).length - slots.size,
    },
    quality: {
      declared_level: declaredQuality,
      values: qualityUnique,
      raw_value_counts: countBy(qualityValues),
      marker_files: qualityFiles.length,
      marker_file_list: qualityFiles,
      phase_status_formats: unique(phaseStatusFormats),
      phase_status_format_counts: countBy(phaseStatusFormats),
    },
    book_type: {
      values: bookTypeUnique,
      normalized: bookTypeUnique.length === 1 ? bookTypeUnique[0] : (bookTypeUnique.length ? 'mixed' : null),
    },
    statuses: statusUnique,
    t_ids: {
      in_phases: [...phaseTIds].sort(),
      nonstandard_tokens: [...nonstandardTokens].sort(),
      in_registry: registryTIds.sort(),
      registry_collisions: collisionsForBook.sort(),
    },
    duplicate: {
      work_key: normTitle(name),
      record_id: null,
      candidates: [],
      edition_candidate: false,
    },
    audit_status: auditStatus,
    source_locator: 'not_computed',
    claim_kind: 'not_assigned',
    extraction_confidence: 'not_assigned',
    counterexample: 'not_checked',
    applicability: 'not_checked',
    falsifier: 'not_checked',
    reviewed_at: null,
    audit: {
      flags,
      read_verified: false,
    },
  });
}

// duplicate clusters
const byWork = new Map();
for (const r of records) {
  if (r.record_kind !== 'book') continue;
  if (!byWork.has(r.duplicate.work_key)) byWork.set(r.duplicate.work_key, []);
  byWork.get(r.duplicate.work_key).push(r);
}
const duplicateClusters = [];
for (const [key, members] of byWork.entries()) {
  if (members.length < 2) continue;
  const edition = members.some(r => /2nd|3rd|4th|5th|6th|edition|第\s*\d+\s*版/i.test(r.title));
  const cluster = {
    work_key: key,
    edition_candidate: edition,
    resolution_state: 'candidate',
    members: members.map(r => ({ record_id: r.record_id, title: r.title, index_row: r.index_row, edition_id: r.edition_id })),
  };
  duplicateClusters.push(cluster);
  for (const r of members) {
    r.duplicate.record_id = stableId('work', key);
    r.duplicate.candidates = members.filter(m => m !== r).map(m => ({ record_id: m.record_id, title: m.title }));
    r.duplicate.edition_candidate = edition;
  }
}

// T records: one record per registry row, with the minimum audit fields
for (const r of tRows) {
  r.t_row_id = stableId('trow', `${r.line}:${r.t_id}:${r.signature}`);
}
const recordByExact = new Map(records.map(r => [r.title, r]));
const recordByNorm = new Map(records.map(r => [normTitle(r.title), r]));
const findBookRecord = (book) => recordByExact.get(book) || recordByNorm.get(normTitle(book)) || null;
const tRecords = tRows.map(r => {
  const rowsForId = tById.get(r.t_id) || [];
  const colliding = rowsForId.length > 1;
  const exactDuplicate = colliding && new Set(rowsForId.map(x => x.signature)).size === 1;
  const firstRow = rowsForId[0];
  const bookRecord = findBookRecord(r.book);
  return {
    record_id: r.t_row_id,
    t_id: r.t_id,
    t_registry_line: r.line,
    work_id: bookRecord ? bookRecord.work_id : null,
    edition_id: bookRecord ? bookRecord.edition_id : null,
    book_record_id: bookRecord ? bookRecord.record_id : null,
    book: r.book,
    schema_shape: r.schema_shape,
    cells_n: r.cells_n,
    raw_cells: r.raw_cells,
    source_locator: 'not_computed',
    claim_kind: 'not_assigned',
    extraction_confidence: 'not_assigned',
    counterexample: 'not_checked',
    applicability: 'not_checked',
    falsifier: 'not_checked',
    reviewed_at: null,
    audit_status: colliding ? 't_id_collision' : 't_registry_unreviewed',
    dedup: {
      duplicate_of: exactDuplicate && firstRow && r.t_row_id !== firstRow.t_row_id ? firstRow.t_row_id : null,
      alias_paths: [],
      legacy_t_id: null,
      canonical_t_id: exactDuplicate ? r.t_id : (colliding ? null : r.t_id),
      collision_reason: colliding ? (exactDuplicate ? 'exact_duplicate' : 'same_id_different_payload') : null,
      resolution_state: colliding ? (exactDuplicate ? 'exact_duplicate' : 'ambiguous') : 'unique',
    },
  };
});

// T id phase/registry comparison
const phaseTIdsAll = unique(records.flatMap(r => r.t_ids.in_phases));
const nonstandardPhaseTokens = unique(records.flatMap(r => r.t_ids.nonstandard_tokens));
const tMissingFromRegistry = phaseTIdsAll.filter(id => !tRegistryIds.has(id));
const tRegistryNotInPhases = [...tRegistryIds].filter(id => !phaseTIdsAll.includes(id));
const registryBooksUnmatched = unique(tRows.map(r => r.book).filter(Boolean).filter(b => !dirNames.includes(b) && !records.some(r => normTitle(r.title) === normTitle(b))));

// cross-book signal index has its own T namespace; check it against registry + phases
const crossIndexIds = unique([...crossIndexRaw.matchAll(T_TOKEN)].map(m => m[0]).filter(id => T_ID_CANON.test(id))).sort();
const crossIndexNotInRegistry = crossIndexIds.filter(id => !tRegistryIds.has(id));
const crossIndexInPhases = crossIndexIds.filter(id => phaseTIdsAll.includes(id));
const crossIndexNotInPhases = crossIndexIds.filter(id => !phaseTIdsAll.includes(id));

// ---------- summary ----------
const books = records.filter(r => r.record_kind === 'book');
const nonbooks = records.filter(r => r.record_kind !== 'book');
const indexedNames = new Set(indexRows.map(r => r.name));
const dirNameSet = new Set(dirNames);
const indexOnly = [...indexedNames].filter(n => !dirNameSet.has(n));
const dirOnly = dirNames.filter(n => !indexedNames.has(n));
const qualityBooks = books.filter(r => r.quality.marker_files > 0);
const qualityBooksJingdu = books.filter(r => r.quality.declared_level === '精读级');
const qualityBooksFramework = books.filter(r => r.quality.declared_level === '框架级');
const qualityBooksUnmarked = books.filter(r => r.quality.marker_files === 0);
const qualityBooksMixed = books.filter(r => r.quality.declared_level === 'mixed');
const phaseComplete = books.filter(r => r.phase.slot_count === 6);
const phaseIncomplete = books.filter(r => r.phase.files_total > 0 && r.phase.slot_count < 6);
const phaseExtra = books.filter(r => r.phase.extra_files > 0);
const booksWithLegacy = books.filter(r => r.phase.legacy_files > 0);
const booksWithFulltext = books.filter(r => r.presence.fulltext_main);
const booksWithoutFulltext = books.filter(r => !r.presence.fulltext_main);
const booksWithTextAny = books.filter(r => r.presence.fulltext_any || r.presence.source_files > 0);
const booksWithoutAnyText = books.filter(r => !r.presence.fulltext_any && !r.presence.source_files);
const booksNeedingConversion = books.filter(r => !r.presence.fulltext_any && r.presence.source_files > 0);
const booksWithSource = books.filter(r => r.presence.source_files > 0);
const booksWithoutSource = books.filter(r => !r.presence.source_files);
const booksWithSkeleton = books.filter(r => r.presence.skeleton);
const booksWithoutSkeleton = books.filter(r => !r.presence.skeleton);
const phaseLikeFiles = records.reduce((n, r) => n + r.phase.files_total, 0);
const canonicalPhaseFiles = records.reduce((n, r) => n + r.phase.canonical_files, 0);
const variantPhaseFiles = records.reduce((n, r) => n + r.phase.variant_files, 0);
const legacyPhaseFiles = records.reduce((n, r) => n + r.phase.legacy_files, 0);
const qualityMarkerFiles = records.reduce((n, r) => n + r.quality.marker_files, 0);
const qualityMarkerFilesJingdu = records.reduce((n, r) => n + (r.quality.raw_value_counts['精读级'] || 0), 0);
const qualityMarkerFilesFramework = records.reduce((n, r) => n + (r.quality.raw_value_counts['框架级'] || 0), 0);
const phaseStatusCommentFiles = records.reduce((n, r) => n + (r.quality.phase_status_format_counts.comment || 0), 0);
const phaseStatusYamlFiles = records.reduce((n, r) => n + (r.quality.phase_status_format_counts.yaml || 0), 0);
const phaseStatusLooseFiles = records.reduce((n, r) => n + (r.quality.phase_status_format_counts.loose || 0), 0);
const phaseStatusUnparsedFiles = records.reduce((n, r) => n + (r.quality.phase_status_format_counts.unparsed || 0), 0);
const phaseStatusMissingFiles = phaseLikeFiles - qualityMarkerFiles - phaseStatusUnparsedFiles;
const qualityBooksOther = books.filter(r => r.quality.marker_files > 0 && r.quality.declared_level !== '精读级' && r.quality.declared_level !== '框架级');

const summary = {
  indexed_rows: indexRows.length,
  dir_records: dirNames.length,
  book_records: books.length,
  nonbook_records: nonbooks.length,
  nonbook_indexed: nonbooks.filter(r => r.indexed).length,
  nonbook_dir_only: nonbooks.filter(r => !r.indexed).length,
  index_only_records: indexOnly.length,
  dir_only_records: dirOnly.length,
  unique_works_provisional: byWork.size,
  duplicate_clusters: duplicateClusters.length,
  duplicate_candidate_records: duplicateClusters.reduce((n, c) => n + c.members.length - 1, 0),
  edition_candidate_clusters: duplicateClusters.filter(c => c.edition_candidate).length,
  phase_like_files: phaseLikeFiles,
  canonical_phase_files: canonicalPhaseFiles,
  variant_phase_files: variantPhaseFiles,
  legacy_phase_files: legacyPhaseFiles,
  phase_complete_books: phaseComplete.length,
  phase_incomplete_books: phaseIncomplete.length,
  phase_books_with_extra_files: phaseExtra.length,
  books_with_legacy_files: booksWithLegacy.length,
  books_with_source: booksWithSource.length,
  books_without_source: booksWithoutSource.length,
  books_with_fulltext: booksWithFulltext.length,
  books_without_fulltext: booksWithoutFulltext.length,
  books_with_text_any: booksWithTextAny.length,
  books_without_any_text: booksWithoutAnyText.length,
  books_needing_conversion: booksNeedingConversion.length,
  books_with_skeleton: booksWithSkeleton.length,
  books_without_skeleton: booksWithoutSkeleton.length,
  quality_marker_files: qualityMarkerFiles,
  quality_marker_files_jingdu: qualityMarkerFilesJingdu,
  quality_marker_files_framework: qualityMarkerFilesFramework,
  quality_marker_books: qualityBooks.length,
  quality_marker_books_jingdu: qualityBooksJingdu.length,
  quality_marker_books_framework: qualityBooksFramework.length,
  quality_marker_books_other: qualityBooksOther.length,
  quality_books_unmarked: qualityBooksUnmarked.length,
  quality_books_mixed: qualityBooksMixed.length,
  phase_status_comment_files: phaseStatusCommentFiles,
  phase_status_yaml_files: phaseStatusYamlFiles,
  phase_status_loose_files: phaseStatusLooseFiles,
  phase_status_unparsed_files: phaseStatusUnparsedFiles,
  phase_status_missing_files: phaseStatusMissingFiles,
  t_registry_total_rows: tRows.length,
  t_registry_unique_ids: tById.size,
  t_registry_extra_occurrences: tRows.length - tById.size,
  t_registry_colliding_ids: tCollisions.length,
  t_registry_ambiguous_ids: tAmbiguous.length,
  t_registry_exact_duplicate_ids: tExactDuplicates.length,
  t_registry_distribution: tCountDist,
  t_registry_schema_shapes: tSchemaShapes,
  t_registry_schema_mismatch_rows: tRows.filter(r => r.schema_shape !== 'v1_8col').length,
  t_registry_field_mapping_ambiguous_rows: tFieldMappingAmbiguous.length,
  t_registry_parse_failures: tParseFailures.length,
  registry_rows_with_depends_on: registryRowsWithDependsOn,
  registry_rows_with_conflicts_with: registryRowsWithConflictsWith,
  phase_files_with_depends_on: phaseFilesWithDependsOn,
  phase_files_with_conflicts_with: phaseFilesWithConflictsWith,
  cross_index_ids_unique: crossIndexIds.length,
  cross_index_ids_not_in_registry: crossIndexNotInRegistry.length,
  cross_index_ids_in_phases: crossIndexInPhases.length,
  cross_index_ids_not_in_phases: crossIndexNotInPhases.length,
  t_records_total: tRecords.length,
  t_records_collision: tRecords.filter(r => r.audit_status === 't_id_collision').length,
  t_records_unreviewed: tRecords.filter(r => r.audit_status === 't_registry_unreviewed').length,
  t_ids_in_phases_unique: phaseTIdsAll.length,
  nonstandard_t_tokens_in_phases: nonstandardPhaseTokens.length,
  t_ids_in_phases_not_in_registry: tMissingFromRegistry.length,
  t_ids_in_registry_not_in_phases: tRegistryNotInPhases.length,
  read_verified_books: books.filter(r => r.audit.read_verified).length,
};

const checks = {
  phase_six_slots: { pass: phaseComplete.length, fail: phaseIncomplete.length, total: books.length },
  source_present: { pass: booksWithSource.length, fail: booksWithoutSource.length, total: books.length },
  text_available: { pass: booksWithTextAny.length, fail: booksWithoutAnyText.length, total: books.length },
  fulltext_present: { pass: booksWithFulltext.length, fail: booksWithoutFulltext.length, total: books.length },
  skeleton_present: { pass: booksWithSkeleton.length, fail: booksWithoutSkeleton.length, total: books.length },
  quality_marker_present: { pass: qualityBooks.length, fail: qualityBooksUnmarked.length, total: books.length },
  phase_status_parseable: { pass: qualityMarkerFiles, fail: phaseStatusMissingFiles, total: phaseLikeFiles },
  t_registry_unique: { pass: tById.size, fail: tRows.length - tById.size, total: tRows.length },
  t_registry_schema_consistent: { pass: tRows.filter(r => r.schema_shape === 'v1_8col').length, fail: tRows.filter(r => r.schema_shape !== 'v1_8col').length, total: tRows.length },
  read_verified: { pass: summary.read_verified_books, fail: books.length - summary.read_verified_books, total: books.length },
};

const knownIssues = [
  {
    code: 'COUNT_BASIS_MISMATCH',
    severity: 'high',
    evidence: `index.csv=${summary.indexed_rows}; dirs=${summary.dir_records}; books=${summary.book_records}; unique_works_provisional=${summary.unique_works_provisional}`,
    description: 'README/总索引的 110/111、index.csv 的 132、目录数 133、书目数 128 是不同统计对象，不能互相替代。',
    resolution_state: 'open',
  },
  {
    code: 'QUALITY_FILE_VS_BOOK',
    severity: 'high',
    evidence: `quality_marker_files=${summary.quality_marker_files} (精读 ${summary.quality_marker_files_jingdu} / 框架 ${summary.quality_marker_files_framework}); quality_marker_books=${summary.quality_marker_books} (精读 ${summary.quality_marker_books_jingdu} / 框架 ${summary.quality_marker_books_framework})`,
    description: '上一轮把 73/12 个“文件标记”说成了书目数量；按书聚合后是 18 本精读 / 4 本框架。',
    resolution_state: 'open',
  },
  {
    code: 'PHASE_STATUS_MISSING',
    severity: 'high',
    evidence: `phase_like_files=${summary.phase_like_files}; quality_marker_files=${summary.quality_marker_files}; comment=${summary.phase_status_comment_files}; yaml=${summary.phase_status_yaml_files}; loose=${summary.phase_status_loose_files}; missing=${summary.phase_status_missing_files}`,
    description: '绝大多数 Phase 文件没有可解析的 phase_status/quality_level；当前状态字段不能支撑全库验收。',
    resolution_state: 'open',
  },
  {
    code: 'T_REGISTRY_SCHEMA_MIXED',
    severity: 'high',
    evidence: `rows=${summary.t_registry_total_rows}; 8-col=${summary.t_registry_schema_shapes.v1_8col || 0}; 12-col=${summary.t_registry_schema_shapes.v2_12col || 0}; ambiguous_field_mapping=${summary.t_registry_field_mapping_ambiguous_rows}`,
    description: 'T 注册表表头是 8 列，但 268 行使用 12 列形态，字段语义不一致，不能直接机器解析。',
    resolution_state: 'open',
  },
  {
    code: 'T_ID_COLLISIONS',
    severity: 'high',
    evidence: `rows=${summary.t_registry_total_rows}; unique=${summary.t_registry_unique_ids}; extra=${summary.t_registry_extra_occurrences}; colliding=${summary.t_registry_colliding_ids}; ambiguous=${summary.t_registry_ambiguous_ids}; exact_duplicate=${summary.t_registry_exact_duplicate_ids}; distribution=${JSON.stringify(summary.t_registry_distribution)}`,
    description: '72 个编号发生碰撞，其中 68 个指向不同内容；不允许自动重编号或让一个旧 ID 指向两处。',
    resolution_state: 'open',
  },
  {
    code: 'DUPLICATE_WORKS',
    severity: 'medium',
    evidence: `clusters=${summary.duplicate_clusters}; extra_records=${summary.duplicate_candidate_records}; edition_clusters=${summary.edition_candidate_clusters}`,
    description: '同一作品的重复目录只是候选，需人工确认；版本差异不能直接判为重复。',
    resolution_state: 'open',
  },
  {
    code: 'PATH_REFERENCE_STALE',
    severity: 'medium',
    evidence: 'SKILL.md 引用 A_已完成_可调用技能/辅助产出/T注册表.md 与 .../跨书知识索引.md；实际为 F_辅助产出/T注册表.md 与 F_辅助产出/跨书信号索引.md',
    description: 'Skill 中的路径与实际目录不一致，需在后续版本修正引用，而不是按文本猜路径。',
    resolution_state: 'open',
  },
  {
    code: 'CROSS_INDEX_REGISTRY_DISCONNECT',
    severity: 'high',
    evidence: `cross_index_ids=${summary.cross_index_ids_unique}; in_registry=${summary.cross_index_ids_unique - summary.cross_index_ids_not_in_registry}; not_in_registry=${summary.cross_index_ids_not_in_registry}; in_phases=${summary.cross_index_ids_in_phases}; not_in_phases=${summary.cross_index_ids_not_in_phases}`,
    description: '跨书信号索引使用独立 T 命名空间：47 个 T ID 全部不在 T 注册表，其中 39 个也未在 Phase 文件出现。不能把这些 ID 当作已注册或已验证知识。',
    resolution_state: 'open',
  },
  {
    code: 'DEPENDENCY_FIELDS_ABSENT',
    severity: 'high',
    evidence: `registry depends_on=${summary.registry_rows_with_depends_on}; conflicts_with=${summary.registry_rows_with_conflicts_with}; phase depends_on=${summary.phase_files_with_depends_on}; conflicts_with=${summary.phase_files_with_conflicts_with}`,
    description: 'T 注册表和 Phase 文件都没有 depends_on / conflicts_with 字段，无法执行悬空依赖检查；不能把“未发现悬空”当成通过。',
    resolution_state: 'open',
  },
  {
    code: 'READ_NOT_VERIFIED',
    severity: 'high',
    evidence: `read_verified_books=${summary.read_verified_books}/${summary.book_records}`,
    description: '本索引只做 Inventory 级校验；没有任何一本书完成 source_locator/claim_kind/抽样核对的完整验收。',
    resolution_state: 'open',
  },
];

// ---------- readback samples ----------
const normWs = (s) => s.replace(/[\s\u00a0]+/g, ' ').trim();

function scanSource(bookTitle, sourceRel, patterns, maxHits = 5) {
  const fileRel = `A_已完成_可调用技能/${bookTitle}/${sourceRel}`;
  const full = path.join(ROOT, fileRel);
  if (!existsFile(full)) {
    return { file: fileRel, exists: false, checks: patterns.map(p => ({ label: p.label, found: false, hits: [] })) };
  }
  const lines = read(full).split(/\r?\n/);
  const checks = patterns.map(p => {
    const hits = [];
    for (let i = 0; i < lines.length && hits.length < maxHits; i++) {
      const normalized = normWs(lines[i]);
      if (normalized && p.pattern.test(normalized)) {
        hits.push({ line: i + 1, excerpt: normalized.slice(0, p.excerptLen || 120) });
      }
    }
    return { label: p.label, found: hits.length > 0, hits };
  });
  return { file: fileRel, exists: true, lines_total: lines.length, checks };
}

function findLineHits(text, needle) {
  return text.split(/\r?\n/)
    .map((line, i) => ({ line: i + 1, text: line }))
    .filter(x => x.text.includes(needle))
    .map(x => ({ line: x.line, excerpt: normWs(x.text).slice(0, 160) }));
}

const registryRowsFor = (id) => (tById.get(id) || []).map(r => ({
  line: r.line,
  book: r.book,
  schema_shape: r.schema_shape,
  cells_n: r.cells_n,
  field_3: r.field_3,
  field_4: r.field_4,
  field_5: r.field_5,
  signature: r.signature,
}));

const READBACK_CONFIG = [
  {
    t_id: 'T-SHM-010',
    sample_kind: 'random',
    selection_method: 'sorted 286 phase T IDs; sha256("2026-10-20") prefix f39a418b mod 286 = 207',
    book: 'Trading In the Shadow of the Smart Money (Gavin Holmes)',
    source_rel: '全书文本/全书文字.md',
    patterns: [
      { label: 'up_bar_definition', pattern: /closed higher than the previous bar/i },
      { label: 'down_bar_definition', pattern: /closed lower than the bar\(s\) behind it/i },
      { label: 'weakness_on_up_bar', pattern: /weakness appears on an up bar/i },
      { label: 'strength_on_down_bar', pattern: /strength appears on a down bar/i },
    ],
    assessment: 'Phase1 对 Up bar / Down bar 的定义与原文一致；原文规则 1/2 给出同一定义。但 T-SHM-010 不在 T 注册表（0 行），双向一致性失败。原文词间夹制表符/双空格，朴素 grep 会漏检；source_locator 必须做空白归一化。',
    status: 'source_supported_but_unregistered',
  },
  {
    t_id: 'T-LIV-003',
    sample_kind: 'key',
    selection_method: 'key sample: trend/sector filter, prioritized after T-LIV-002 stop-loss',
    book: 'How to Trade In Stocks (Jesse Livermore)',
    source_rel: '全书文本/全书文字.md',
    patterns: [
      { label: 'industry_group_concept', pattern: /industry group/i },
      { label: 'sister_stock_concept', pattern: /sister stock/i },
      { label: 'top20_parameter', pattern: /top\s*20|20\s*%/i },
      { label: 'chapter5_body_heading', pattern: /^chapter\s*-?\s*5(\s*-?\s*l)?$/i },
      { label: 'chapter8_body_heading', pattern: /^chapter\s*8$/i },
    ],
    assessment: '概念有原文支撑，实际位于 Chapter 8（Industry Group / Tandem Trading），但 Phase2 标注 Ch5，定位错误。`sector_momentum_top20%` 在原文只对应 20%-20%-20%-40% 建仓比例（lines 928/1706），不是板块动量阈值。注册表两行（98/105）载荷不同，判 ambiguous collision。',
    status: 'concept_supported_locator_and_parameter_unsupported',
  },
  {
    t_id: 'T-VPA-001',
    sample_kind: 'controversial',
    selection_method: 'controversial sample: low-volume/false-breakout family has the most conflicting rows',
    book: 'A Complete Guide To Volume Price Analysis (Coulling, Anna)',
    source_rel: '全书文本/全书文字.md',
    patterns: [
      { label: 'breakout_with_volume', pattern: /breakout from one of these regions of consolidation, coupled with volume/i },
      { label: 'rising_prices_rising_volume', pattern: /rising prices = rising volume/i },
      { label: 'threshold_1_5', pattern: /1\.5|150\s*%/i },
      { label: 'chapter4_heading', pattern: /^chapter four$/i },
      { label: 'chapter7_heading', pattern: /^chapter seven$/i },
      { label: 'chapter8_heading', pattern: /^chapter eight$/i },
    ],
    assessment: '概念有原文支撑（放量突破/价量齐升），Chapter 4 与 Chapter 7/8 覆盖范围基本相符；但 `vol>=1.5x_avg` 是派生阈值，原文没有。Phase2 仅 29 行骨架，无 source_locator/claim_kind。注册表两行（14/22）载荷不同，判 ambiguous collision。',
    status: 'concept_supported_parameter_derived',
  },
  {
    t_id: 'T-AMH-01-01',
    sample_kind: 'cross_index_discovery',
    selection_method: 'discovered while cross-checking 跨书信号索引; included as an extra finding, not one of the three required samples',
    book: 'Adaptive Markets (Andrew Lo)',
    source_rel: '全书文本/Adaptive_Markets_全书文本.md',
    patterns: [
      { label: 'adaptive_markets_hypothesis_zh', pattern: /適應性市場假說/i },
      { label: 'maladaptation_zh', pattern: /適應不良/i },
    ],
    assessment: '跨书信号索引 5 次引用并标“精读级”，但 T 注册表 0 行、Phase 文件 0 次出现；Adaptive Markets 的 Phase2 有 6 条实质信号却完全没有 T ID。该编号是悬空引用（phantom），不能用于追溯。',
    status: 'phantom_cross_index_only',
  },
];

const readbackPhaseHits = {};
for (const s of READBACK_CONFIG) readbackPhaseHits[s.t_id] = [];
for (const r of records) {
  for (const f of r.phase.file_list) {
    const full = path.join(ROOT, f);
    const lines = read(full).split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      for (const s of READBACK_CONFIG) {
        if (lines[i].includes(s.t_id)) {
          readbackPhaseHits[s.t_id].push({
            record_id: r.record_id,
            title: r.title,
            file: f,
            line: i + 1,
            excerpt: normWs(lines[i]).slice(0, 160),
          });
        }
      }
    }
  }
}

const readbackSamples = READBACK_CONFIG.map(s => ({
  t_id: s.t_id,
  sample_kind: s.sample_kind,
  selection_method: s.selection_method,
  book: s.book,
  status: s.status,
  registry_rows: registryRowsFor(s.t_id),
  phase_hits: readbackPhaseHits[s.t_id],
  cross_index_hits: findLineHits(crossIndexRaw, s.t_id),
  source_checks: scanSource(s.book, s.source_rel, s.patterns),
  assessment: s.assessment,
  read_verified: false,
}));

const markedBooks = books.filter(r => ['精读级', '框架级'].includes(r.quality.declared_level));
const qualityBookProbes = markedBooks.map(r => {
  const p2rel = r.phase.file_list.find(f => /Phase2/.test(f)) || null;
  let p2 = { file: p2rel, exists: false, t_ids: [], t_ids_registered: [], stub: false, locator_signals: 0 };
  if (p2rel) {
    const text = read(path.join(ROOT, p2rel));
    const tids = unique([...text.matchAll(T_TOKEN)].map(m => m[0]).filter(id => T_ID_CANON.test(id)));
    p2 = {
      file: p2rel,
      exists: true,
      lines: text.split(/\r?\n/).length,
      bytes: Buffer.byteLength(text),
      sha256: sha256(text),
      t_ids: tids,
      t_ids_registered: tids.filter(id => tRegistryIds.has(id)),
      stub: /待精读/.test(text) && tids.length === 0,
      locator_signals: (text.match(/\bCh\s*\d+|第\s*\d+\s*章|p\.\s*\d+|(?<![A-Za-z])L\d+|line\s*\d+/gi) || []).length,
    };
  }
  const markerFiles = [];
  for (const f of r.phase.file_list) {
    const text = read(path.join(ROOT, f));
    const m = text.match(/^\s*quality_level\s*:\s*([^\n\r#]+)/m);
    if (m) markerFiles.push({ file: path.basename(f), level: m[1].trim() });
  }
  return {
    record_id: r.record_id,
    title: r.title,
    declared_level: r.quality.declared_level,
    phase_files_total: r.phase.files_total,
    marker_files: markerFiles,
    marker_coverage: markerFiles.length === r.phase.files_total ? 'all' : (markerFiles.length ? 'partial' : 'none'),
    phase2: p2,
    source_text: r.presence.fulltext_main || r.presence.fulltext_files > 0,
  };
});

const tierProbes = (tier) => qualityBookProbes.filter(x => x.declared_level === tier);
const stubProbes = qualityBookProbes.filter(x => x.phase2.stub);
const jingduProbes = tierProbes('精读级');
const frameworkProbes = tierProbes('框架级');
const jingduWithTids = jingduProbes.filter(x => x.phase2.t_ids.length > 0);
const frameworkWithTids = frameworkProbes.filter(x => x.phase2.t_ids.length > 0);
const allMarkedPhase2Tids = unique(qualityBookProbes.flatMap(x => x.phase2.t_ids));

function scanSourceWindow(bookTitle, sourceRel, patterns) {
  const fileRel = `A_已完成_可调用技能/${bookTitle}/${sourceRel}`;
  const full = path.join(ROOT, fileRel);
  if (!existsFile(full)) return { file: fileRel, exists: false, checks: patterns.map(p => ({ label: p.label, found: false, hits: [] })) };
  const text = read(full);
  const checks = patterns.map(p => {
    const re = new RegExp(p.pattern.source, p.pattern.flags.includes('i') ? 'i' : '');
    const m = re.exec(text);
    if (!m) return { label: p.label, found: false, hits: [] };
    const s = Math.max(0, m.index - 100);
    const e = Math.min(text.length, m.index + m[0].length + 120);
    return { label: p.label, found: true, hits: [{ char_offset: m.index, excerpt: text.slice(s, e).replace(/\s+/g, ' ') }] };
  });
  return { file: fileRel, exists: true, chars: text.length, checks };
}

const qualityContentSample = {
  book: 'Narrative Economics (Shiller)',
  level: '精读级',
  phase2: (() => {
    const p = qualityBookProbes.find(x => x.title === 'Narrative Economics (Shiller)');
    return p ? { lines: p.phase2.lines, bytes: p.phase2.bytes, t_ids: p.phase2.t_ids, locator_signals: p.phase2.locator_signals } : null;
  })(),
  source_checks: scanSourceWindow('Narrative Economics (Shiller)', '全书文本/全书文字.md', [
    { label: 'contagion_rate', pattern: /contagion rate/i },
    { label: 'recovery_rate', pattern: /recovery rate/i },
    { label: 'sir_acronym', pattern: /\bSIR\b/ },
    { label: 'threshold_15pct', pattern: /15\s*%/ },
    { label: 'threshold_10pct', pattern: /10\s*%/ },
  ]),
  assessment: 'SIR 的传染率/恢复率框架与 Kermack-McKendrick SIR 模型均有原文支撑（contagion rate / recovery rate / SIR 均命中）；但 15%/10% 周阈值在原文无命中（10% 仅出现在无关例子）。Phase2 有 105 行实质框架卡，但 0 个 T 编号、0 个章节/行号定位。',
};

const qualityReadback = {
  marked_books: qualityBookProbes.length,
  by_tier: { 精读级: jingduProbes.length, 框架级: frameworkProbes.length },
  phase2_stub_books: stubProbes.map(x => x.title),
  phase2_stub_signatures: countBy(stubProbes.map(x => x.phase2.sha256.slice(0, 10))),
  phase2_substantive_books: qualityBookProbes.filter(x => x.phase2.exists && !x.phase2.stub).length,
  phase2_with_t_ids: qualityBookProbes
    .filter(x => x.phase2.t_ids.length > 0)
    .map(x => ({ title: x.title, level: x.declared_level, t_ids: x.phase2.t_ids, registered: x.phase2.t_ids_registered.length })),
  phase2_t_ids_unique: allMarkedPhase2Tids.length,
  phase2_t_ids_registered: allMarkedPhase2Tids.filter(id => tRegistryIds.has(id)).length,
  phase2_t_ids_in_cross_index: allMarkedPhase2Tids.filter(id => crossIndexIds.includes(id)).length,
  phase2_t_ids_in_cross_index_list: allMarkedPhase2Tids.filter(id => crossIndexIds.includes(id)).sort(),
  books_with_locator_signal: qualityBookProbes
    .filter(x => x.phase2.locator_signals > 0)
    .map(x => ({ title: x.title, level: x.declared_level, signals: x.phase2.locator_signals })),
  marker_coverage: countBy(qualityBookProbes.map(x => x.marker_coverage)),
  marker_level_mismatch_books: qualityBookProbes
    .filter(x => new Set(x.marker_files.map(m => m.level)).size > 1)
    .map(x => x.title),
  tier_inversion: {
    jingdu_with_t_ids: jingduWithTids.length,
    framework_with_t_ids: frameworkWithTids.length,
  },
  books: qualityBookProbes,
  content_sample: qualityContentSample,
  findings: [
    `${stubProbes.length}/${jingduProbes.length} 精读级书籍的 Phase2 是“待精读”模板（${Object.keys(countBy(stubProbes.map(x => x.phase2.sha256.slice(0, 10)))).length} 个相同哈希），没有信号、没有 T 编号；精读级标记只落在 Phase0 文件上（部分为非标准文件名）。`,
    `${jingduWithTids.length}/${jingduProbes.length} 精读级 Phase2 含 T 编号；${frameworkWithTids.length}/${frameworkProbes.length} 框架级含 T 编号，但 ${allMarkedPhase2Tids.filter(id => tRegistryIds.has(id)).length}/${allMarkedPhase2Tids.length} 个已注册。层级倒挂：只有较低层级使用 T 编号，且全部未注册。`,
    `仅 ${qualityBookProbes.filter(x => x.phase2.locator_signals > 0).length}/${qualityBookProbes.length} 本已标书的 Phase2 出现章节/行号定位信号；其余没有可复核定位。`,
    `标记覆盖：${countBy(qualityBookProbes.map(x => x.marker_coverage)).all || 0} 本全部 Phase 文件都有标记，${countBy(qualityBookProbes.map(x => x.marker_coverage)).partial || 0} 本仅部分文件有标记。`,
    `框架级 T 编号中 ${allMarkedPhase2Tids.filter(id => crossIndexIds.includes(id)).length} 个只出现在跨书信号索引，注册表仍为 0。`,
  ],
};

const textBearingSibling = (rec) => {
  const cluster = duplicateClusters.find(c => c.members.some(m => m.record_id === rec.record_id));
  if (!cluster) return false;
  return cluster.members.some(m => {
    if (m.record_id === rec.record_id) return false;
    const sibling = records.find(x => x.record_id === m.record_id);
    return sibling && (sibling.presence.fulltext_main || sibling.presence.fulltext_files > 0);
  });
};

const sourceBlockedRecords = books.filter(r => r.audit_status === 'source_blocked');
const phaseIncompleteRecords = books.filter(r => r.audit_status === 'phase_incomplete');

const readback = {
  generated_at: null,
  protocol: 'DECISION §5: 1 random + 1 key + 1 controversial T; 3 passing samples do not verify a whole book.',
  random_seed: '2026-10-20',
  samples: readbackSamples,
  sample_status_counts: countBy(readbackSamples.map(s => s.status)),
  cross_index_reference_check: {
    t_id: 'T-AMH-01-01',
    registry_rows: registryRowsFor('T-AMH-01-01').length,
    phase_hits: readbackPhaseHits['T-AMH-01-01'].length,
    cross_index_hits: findLineHits(crossIndexRaw, 'T-AMH-01-01').length,
    cross_index_labels_quality: /T-AMH-01-01[^\n]*精读级/.test(crossIndexRaw),
    adaptive_phase_t_ids: unique((records.find(r => r.title === 'Adaptive Markets (Andrew Lo)') || { t_ids: { in_phases: [] } }).t_ids.in_phases),
    note: '跨书信号索引是独立命名空间；47 个唯一 T ID 全部不在注册表，只有 8 个在 Phase 出现。',
  },
  duplicate_cluster_review: duplicateClusters.map(c => ({
    work_key: c.work_key,
    edition_candidate: c.edition_candidate,
    members: c.members.map(m => {
      const rec = records.find(x => x.record_id === m.record_id);
      return {
        record_id: m.record_id,
        title: m.title,
        audit_status: rec ? rec.audit_status : null,
        phase_slots: rec ? rec.phase.slot_count : null,
        text_available: rec ? (rec.presence.fulltext_main || rec.presence.fulltext_files > 0) : null,
      };
    }),
    action: 'keep_both_pending_review',
  })),
  source_blocked_review: {
    total: sourceBlockedRecords.length,
    records: sourceBlockedRecords.map(r => ({
      record_id: r.record_id,
      title: r.title,
      text_bearing_sibling: textBearingSibling(r),
    })),
    standalone_no_text: sourceBlockedRecords.filter(r => !textBearingSibling(r)).map(r => r.title),
  },
  phase_incomplete_review: {
    total: phaseIncompleteRecords.length,
    records: phaseIncompleteRecords.map(r => ({
      record_id: r.record_id,
      title: r.title,
      slots: r.phase.slots,
      complete_sibling: (() => {
        const cluster = duplicateClusters.find(c => c.members.some(m => m.record_id === r.record_id));
        if (!cluster) return false;
        return cluster.members.some(m => {
          if (m.record_id === r.record_id) return false;
          const sibling = records.find(x => x.record_id === m.record_id);
          return sibling && sibling.phase.slot_count === 6;
        });
      })(),
    })),
  },
  quality_readback: qualityReadback,
  limits: [
    '4 samples inspected, not 128 books.',
    '22 marked books inspected at Phase2/marker level; this is not a full content verification.',
    'All sample read_verified=false: no book-level source_locator/claim_kind/counterexample/applicability/falsifier completion.',
    'Excerpts are whitespace-normalized short fragments for locating only; no long text is reproduced.',
  ],
};

const generatedAt = new Date().toISOString();
readback.generated_at = generatedAt;
const indexJson = {
  schema_version: 'bookknowledge.index.v1',
  generated_at: generatedAt,
  generator: 'build_bookknowledge_index.js',
  source_root_label: 'BOOKKNOWLEDGE_DIR',
  definitions: {
    indexed_rows: 'index.csv 中的数据行数',
    dir_records: 'A_已完成_可调用技能 下的直接子目录数',
    book_records: 'dir_records 中排除 _ 前缀、SKILL文件 等非书目录后的书目目录数',
    nonbook_records: '非书目录数（含 _孟洪涛×3、SKILL文件、_bk_scratch）',
    unique_works_provisional: '按标题归一化后的临时作品簇数，仅用于去重候选，不是最终作品数',
    phase_complete_books: 'Phase 0–5 六个槽位均存在（canonical 或 variant 名称）的书目数',
    quality_marker_books: '至少一个 Phase 文件包含可解析 quality_level 的书目数；不等于已验收',
    quality_marker_files: '包含 quality_level 的 Phase 文件数；不是书目数',
    read_verified_books: '完成 source_locator、claim_kind、抽样核对并有证据的书目数；当前为 0',
    audit_status: '记录级校验状态；不等于内容质量结论',
    claim_kind: '知识主张类型（方法论/概念框架/数据事实等）；当前统一未分配，待逐条回读后填写',
    source_locator: 'T 编号到全书文字.md 的章节/段落定位；当前未计算，故不得标 PASS',
    t_registry_extra_occurrences: '271 行减去 183 个唯一编号 = 88 次重复出现',
    t_registry_colliding_ids: '出现次数 >1 的 T 编号个数；不是多余行数',
    t_records: 'T 注册表每一行一条知识记录；record_id 为 T 行记录，work_id/edition_id/book_record_id 指向所属书目记录',
    edition_candidate_clusters: '标题归一化后同簇且含版本标记的候选簇；仅候选，未确认',
  },
  inputs: {
    index_csv: { path: rel(INDEX_CSV), sha256: sha256(indexRaw), rows: indexRows.length },
    t_registry: { path: rel(T_REGISTRY), sha256: sha256(tRegistryRaw), rows: tRows.length },
    cross_index: { path: rel(CROSS_INDEX), sha256: sha256(read(CROSS_INDEX)), lines: read(CROSS_INDEX).split(/\r?\n/).length },
    phase_files: { pattern: 'A_已完成_可调用技能/*/拆解记录/**/*Phase*.md', count: phaseLikeFiles, path_list_sha256: sha256(records.flatMap(r => r.phase.file_list).sort().join('\n')) },
  },
  summary,
  checks,
  records,
  t_records: tRecords,
  duplicate_clusters: duplicateClusters,
  t_registry: {
    schema_header_columns: 8,
    schema_shapes: tSchemaShapes,
    distribution: tCountDist,
    collisions: tCollisions.map(([id, a]) => ({
      t_id: id,
      occurrences: a.length,
      distinct_payloads: new Set(a.map(r => r.signature)).size,
      resolution_state: new Set(a.map(r => r.signature)).size === 1 ? 'exact_duplicate' : 'ambiguous',
      rows: a.map(r => ({ line: r.line, book: r.book, schema: r.schema_shape, field_3: r.field_3, field_4: r.field_4 })),
    })),
    parse_failures: tParseFailures.map(r => ({ line: r.line, t_id: r.t_id, book: r.book })),
    registry_books_unmatched: registryBooksUnmatched,
  },
  t_id_crosscheck: {
    phase_ids_unique: phaseTIdsAll.length,
    nonstandard_tokens_unique: nonstandardPhaseTokens.length,
    nonstandard_tokens: nonstandardPhaseTokens.sort(),
    registry_ids_unique: tRegistryIds.size,
    phase_ids_not_in_registry: tMissingFromRegistry.sort(),
    registry_ids_not_in_phases: tRegistryNotInPhases.sort(),
    cross_index_ids_unique: crossIndexIds.length,
    cross_index_ids_not_in_registry: crossIndexNotInRegistry.sort(),
    cross_index_ids_in_phases: crossIndexInPhases.sort(),
    cross_index_ids_not_in_phases: crossIndexNotInPhases.sort(),
  },
  known_issues: knownIssues,
  readback,
};

fs.writeFileSync(path.join(OUT_DIR, 'BOOKKNOWLEDGE_INDEX.json'), JSON.stringify(indexJson, null, 2) + '\n', 'utf8');

// ---------- summary markdown ----------
const md = [];
md.push('# BookKnowledge 只读索引与全量记录校验摘要');
md.push('');
md.push(`- 生成时间：${generatedAt}`);
md.push('- 输入：`A_已完成_可调用技能/index.csv`、`A_已完成_可调用技能/*/拆解记录/**/*Phase*.md`、`F_辅助产出/T注册表.md`、`F_辅助产出/跨书信号索引.md`');
md.push('- 输出：`BOOKKNOWLEDGE_INDEX.json`（机器可读，相对路径，无绝对路径）');
md.push('- 性质：**Inventory 级只读审计**；`read_verified_books = 0`，不代表任何一本书已通过内容验收。');
md.push('');
md.push('## 1. 口径修正');
md.push('');
md.push('| 指标 | 本轮实测 | 说明 |');
md.push('|---|---:|---|');
md.push(`| index.csv 行数 | ${summary.indexed_rows} | 索引条目，含非书 |`);
md.push(`| 目录数 | ${summary.dir_records} | 含 \`_bk_scratch\` |`);
md.push(`| 书目目录 | ${summary.book_records} | 排除 5 个非书目录 |`);
md.push(`| 非书目录 | ${summary.nonbook_records} | 3 个 \`_孟洪涛\` + \`SKILL文件\` + \`_bk_scratch\` |`);
md.push(`| 临时唯一作品簇 | ${summary.unique_works_provisional} | 标题归一化候选，不是最终作品数 |`);
md.push(`| 重复候选簇 | ${summary.duplicate_clusters} | 含 ${summary.edition_candidate_clusters} 个版本候选 |`);
md.push(`| Phase 类文件 | ${summary.phase_like_files} | canonical ${summary.canonical_phase_files} + variant ${summary.variant_phase_files} + legacy ${summary.legacy_phase_files} |`);
md.push(`| 六槽位齐全的书 | ${summary.phase_complete_books} | 不是上一轮的 115（按“正好 6 个文件”算会误伤多文件书） |`);
md.push(`| 六槽位不齐的书 | ${summary.phase_incomplete_books} | ${phaseIncomplete.map(r => r.title).join('；')} |`);
md.push(`| 有 quality_level 的文件 | ${summary.quality_marker_files} | 精读 ${summary.quality_marker_files_jingdu} / 框架 ${summary.quality_marker_files_framework} |`);
md.push(`| 有 quality_level 的书 | ${summary.quality_marker_books} | 精读 ${summary.quality_marker_books_jingdu} / 框架 ${summary.quality_marker_books_framework}；**上一轮把文件数说成了书目数** |`);
md.push(`| 未标质量级的书 | ${summary.quality_books_unmarked} | 需补 \`phase_status\` |`);
md.push(`| 有源文件的书 | ${summary.books_with_source} | 缺 ${summary.books_without_source} |`);
md.push(`| 有文本可用（全书文本或源文件） | ${summary.books_with_text_any} | 缺 ${summary.books_without_any_text}，这些判 \`source_blocked\` |`);
md.push(`| 有源文件但未转文本 | ${summary.books_needing_conversion} | 判 \`needs_conversion\`，补齐 \`全书文字.md\` 后再拆 |`);
md.push(`| 有标准 \`全书文字.md\` | ${summary.books_with_fulltext} | 缺 ${summary.books_without_fulltext}；非标准命名的 \`*_extract.txt\` 未计入此列 |`);
md.push(`| 有目录骨架的书 | ${summary.books_with_skeleton} | 缺 ${summary.books_without_skeleton} |`);
md.push(`| 已验收书 | ${summary.read_verified_books} | 本索引不做内容验收 |`);
md.push('');
md.push('## 2. Phase 状态字段');
md.push('');
md.push('| 格式 | 文件数 |');
md.push('|---|---:|');
md.push(`| \`<!-- phase_status: ... -->\` | ${summary.phase_status_comment_files} |`);
md.push('| 旧式 YAML 代码块 | ' + summary.phase_status_yaml_files + ' |');
md.push(`| 松散 \`quality_level:\` 行 | ${summary.phase_status_loose_files} |`);
md.push(`| 存在标记但解析失败 | ${summary.phase_status_unparsed_files} |`);
md.push(`| 完全缺失 | ${summary.phase_status_missing_files} |`);
md.push('');
md.push('## 3. T 注册表');
md.push('');
md.push('| 指标 | 数值 |');
md.push('|---|---:|');
md.push(`| 行数 | ${summary.t_registry_total_rows} |`);
md.push(`| 唯一 T ID | ${summary.t_registry_unique_ids} |`);
md.push(`| 额外出现次数 | ${summary.t_registry_extra_occurrences} |`);
md.push(`| 碰撞编号数 | ${summary.t_registry_colliding_ids} |`);
md.push(`| 其中语义不同（ambiguous） | ${summary.t_registry_ambiguous_ids} |`);
md.push(`| 其中完全相同（exact duplicate） | ${summary.t_registry_exact_duplicate_ids} |`);
md.push(`| 8 列行 / 12 列行 | ${summary.t_registry_schema_shapes.v1_8col || 0} / ${summary.t_registry_schema_shapes.v2_12col || 0} |`);
md.push(`| 字段映射不确定行 | ${summary.t_registry_field_mapping_ambiguous_rows} |`);
md.push(`| T ID 解析失败 | ${summary.t_registry_parse_failures} |`);
md.push(`| 每行 T 记录 / 碰撞行 / 未审阅行 | ${summary.t_records_total} / ${summary.t_records_collision} / ${summary.t_records_unreviewed} |`);
md.push('');
md.push(`重复次数分布：${Object.entries(summary.t_registry_distribution).map(([k, v]) => `${k} 次 × ${v} 个编号`).join('；')}。`);
md.push('');
md.push(`Phase 文件引用到的 T ID：${summary.t_ids_in_phases_unique} 个唯一值；不在注册表中的 ${summary.t_ids_in_phases_not_in_registry} 个；注册表有但 Phase 未引用的 ${summary.t_ids_in_registry_not_in_phases} 个。后者可能是索引示例或未落地内容，需人工判断。`);
md.push(`另有 ${summary.nonstandard_t_tokens_in_phases} 个非标准片段（如 T-ALG、T-006），不计入 T ID 交叉核对，已单独列入 \`t_id_crosscheck.nonstandard_tokens\`。`);
md.push(`依赖字段：注册表 \`depends_on\` ${summary.registry_rows_with_depends_on} 行、\`conflicts_with\` ${summary.registry_rows_with_conflicts_with} 行；Phase 文件 \`depends_on\` ${summary.phase_files_with_depends_on} 个、\`conflicts_with\` ${summary.phase_files_with_conflicts_with} 个。字段缺失，悬空依赖检查无法执行。`);
md.push(`跨书信号索引：${summary.cross_index_ids_unique} 个唯一 T ID；${summary.cross_index_ids_not_in_registry} 个不在 T 注册表；仅 ${summary.cross_index_ids_in_phases} 个在 Phase 文件出现，${summary.cross_index_ids_not_in_phases} 个未出现。该索引是独立命名空间，不能当作注册表或已验证知识。`);
md.push('');
md.push('## 4. 记录级审计状态');
md.push('');
md.push('| audit_status | 记录数 |');
md.push('|---|---:|');
const auditStatusDist = countBy(records.map(r => r.audit_status));
for (const [k, v] of Object.entries(auditStatusDist).sort((a, b) => b[1] - a[1])) md.push(`| \`${k}\` | ${v} |`);
md.push('');
md.push('## 5. 回读样本（2026-10-20）');
md.push('');
md.push(`抽样协议：1 随机（seed=\`${readback.random_seed}\`）+ 1 关键 + 1 争议；另加 1 个跨书索引悬空编号。四样本均 \`read_verified=false\`，不构成整书验收。`);
md.push('');
md.push('| T ID | 类型 | 注册表行 | Phase 命中 | 原文核验（首个命中行） | 结论 |');
md.push('|---|---|---:|---:|---|---|');
for (const s of readbackSamples) {
  const src = s.source_checks.exists
    ? (s.source_checks.checks.filter(c => c.found).map(c => `${c.label}:${c.hits.slice(0, 3).map(h => 'L' + h.line).join('|')}`).join('；') || 'no hit')
    : 'source missing';
  md.push(`| \`${s.t_id}\` | ${s.sample_kind} | ${s.registry_rows.length} | ${s.phase_hits.length} | ${src} | \`${s.status}\` |`);
}
md.push('');
md.push(`跨书索引悬空引用：\`T-AMH-01-01\` 在索引出现 ${readback.cross_index_reference_check.cross_index_hits} 次，注册表 ${readback.cross_index_reference_check.registry_rows} 行，Phase ${readback.cross_index_reference_check.phase_hits} 次；Adaptive Markets 全部 Phase 文件共 ${readback.cross_index_reference_check.adaptive_phase_t_ids.length} 个 T ID。`);
md.push('');
md.push(`重复候选 ${readback.duplicate_cluster_review.length} 簇，全部 \`keep_both_pending_review\`，本轮不合并。`);
md.push(`\`source_blocked\` ${readback.source_blocked_review.total} 条：${readback.source_blocked_review.total - readback.source_blocked_review.standalone_no_text.length} 条有含文本的重复候选兄弟（不是独立作品缺口），${readback.source_blocked_review.standalone_no_text.length} 条为无文本独立作品——${readback.source_blocked_review.standalone_no_text.join('；')}。`);
md.push(`\`phase_incomplete\` ${readback.phase_incomplete_review.total} 条：${readback.phase_incomplete_review.records.map(r => `${r.title}（slots ${r.slots.join(',')}，完整兄弟=${r.complete_sibling}）`).join('；')}。`);
md.push('');
md.push('## 6. 质量标记回读（22 本已标书）');
md.push('');
md.push('| 指标 | 数值 |');
md.push('|---|---:|');
md.push(`| 已标书 | ${qualityReadback.marked_books}（精读 ${qualityReadback.by_tier['精读级']} / 框架 ${qualityReadback.by_tier['框架级']}） |`);
md.push(`| Phase2 为“待精读”模板 | ${qualityReadback.phase2_stub_books.length}（同一哈希簇 ${Object.keys(qualityReadback.phase2_stub_signatures).length} 个） |`);
md.push(`| Phase2 有实质内容 | ${qualityReadback.phase2_substantive_books} |`);
md.push(`| Phase2 含 T 编号的书 | ${qualityReadback.phase2_with_t_ids.length}（精读 ${qualityReadback.phase2_with_t_ids.filter(x => x.level === '精读级').length} / 框架 ${qualityReadback.phase2_with_t_ids.filter(x => x.level === '框架级').length}） |`);
md.push(`| Phase2 T 编号唯一值 / 已注册 | ${qualityReadback.phase2_t_ids_unique} / ${qualityReadback.phase2_t_ids_registered} |`);
md.push(`| Phase2 有章节/行号定位信号的书 | ${qualityReadback.books_with_locator_signal.length} |`);
md.push(`| 标记覆盖：全部 / 部分 | ${qualityReadback.marker_coverage.all || 0} / ${qualityReadback.marker_coverage.partial || 0} |`);
md.push('');
md.push(`空壳书（Phase2 为 ${unique(stubProbes.map(x => x.phase2.lines)).join('/')} 行、同一哈希）：${qualityReadback.phase2_stub_books.join('；')}。这些书都有可用文本，不是 \`source_blocked\`；\`quality_level: 精读级\` 与 \`status: done\` 写在 Phase0 文件上（部分为非标准文件名），Phase2 明写“待精读”。`);
md.push(`层级倒挂：${qualityReadback.tier_inversion.jingdu_with_t_ids} 本精读级含 T 编号，${qualityReadback.tier_inversion.framework_with_t_ids} 本框架级含 T 编号；${qualityReadback.phase2_t_ids_unique} 个 Phase2 T 编号中 ${qualityReadback.phase2_t_ids_registered} 个在注册表，${qualityReadback.phase2_t_ids_in_cross_index} 个只在跨书索引。`);
md.push(`有定位信号的书：${qualityReadback.books_with_locator_signal.map(x => `${x.title}（${x.signals} 处）`).join('；') || '无'}。其余已标书没有可复核的章节/行号定位。`);
const qcs = qualityReadback.content_sample;
md.push(`实质样本 Narrative Economics：Phase2 ${qcs.phase2 ? qcs.phase2.lines : '?'} 行、${qcs.phase2 ? qcs.phase2.t_ids.length : '?'} 个 T 编号、${qcs.phase2 ? qcs.phase2.locator_signals : '?'} 个定位。${qcs.assessment}`);
md.push('');
md.push('## 7. 已知问题');
md.push('');
md.push('| 代码 | 严重度 | 证据 | 状态 |');
md.push('|---|---|---|---|');
for (const i of knownIssues) md.push(`| \`${i.code}\` | ${i.severity} | ${i.evidence.replace(/\|/g, '/')} | ${i.resolution_state} |`);
md.push('');
md.push('## 8. 复现');
md.push('');
md.push('```bash');
md.push('cd BookKnowledge');
md.push('BOOKKNOWLEDGE_DIR="$(pwd)" node F_辅助产出/audit/2026-10-20/build_bookknowledge_index.js');
md.push('```');
md.push('');
md.push('脚本只读 `index.csv`、`拆解记录/**/*Phase*.md`、`T注册表.md`、`跨书信号索引.md`，不修改任何书目、Phase、T 编号或 WTOS 生产代码。');
md.push('');
fs.writeFileSync(path.join(OUT_DIR, 'AUDIT_SUMMARY.md'), md.join('\n') + '\n', 'utf8');

console.log(JSON.stringify({
  out_dir: OUT_DIR,
  summary,
  json_bytes: fs.statSync(path.join(OUT_DIR, 'BOOKKNOWLEDGE_INDEX.json')).size,
  summary_bytes: fs.statSync(path.join(OUT_DIR, 'AUDIT_SUMMARY.md')).size,
}, null, 2));
