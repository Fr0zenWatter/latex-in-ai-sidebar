import {preferences, savePreferences, t} from './latex-settings.mjs';
import {attachProofread} from './latex-proofread.mjs';

export function attachProjectReview({editor, request, capture, adopt, open, message, changed=()=>{}}) {
  const button = document.querySelector('#project-review-open');
  const list = document.querySelector('#project-review-list');
  const popover = document.querySelector('#project-review-menu');
  let rows = [], entries = [], files = [], order = [], signature = '', loading = null, committing = false, nextId = 100001;
  const identifiers = new Map();
  const enabled = () => preferences.getItem('latex-codex-proofread-project') === 'on';
  const index = (source, offset) => Array.from(source).slice(0, offset).join('').length;
  function clear() {
    for (const row of rows) { row.review.destroy(); row.marker.clear(); }
    rows = []; entries = []; signature = '';
  }
  async function resolve(file, hunk, action) {
    const state = capture();
    if (committing || !state.clean || state.path === file.path && state.source !== file.source)
      throw new Error(t('请先保存编辑草稿，再处理项目校对。'));
    committing = true;
    const readonly = editor.getOption('readOnly');
    editor.setOption('readOnly', true); rows.forEach(row => row.review.setBusy(true));
    changed();
    try {
      const result = await request('/project-review', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({path:file.path, version:file.version, signature:file.signature, id:hunk?.id, action,
          main_file:state.mainFile, project_root:state.projectRoot,current_path:state.path,current_version:state.version})});
      clear();
      if (result.current_state || result.state.path === state.path) await adopt(result.current_state || result.state);
      render(result);
    } finally {
      committing = false; editor.setOption('readOnly', readonly);
      rows.forEach(row => row.review.setBusy(false)); changed();
    }
  }
  function render(data) {
    files = data.enabled && enabled() ? data.files : [];
    order = data.source_order || files.map(file=>file.path);
    const count = files.reduce((sum, file) => sum + file.hunks.length, 0);
    button.hidden = !count; button.textContent = t('项目校对 · {count}', {count});
    const state = capture(), file = files.find(item => item.path === state.path);
    const visible = preferences.getItem('latex-codex-proofread-editor') !== 'off';
    const next = JSON.stringify([state.path, file && state.source === file.source, visible,
      files.map(file=>[file.path,file.signature]),order]);
    if (next !== signature) {
      clear(); signature = next;
      const retained = new Set();
      for (const sourceFile of files) for (const hunk of sourceFile.hunks) {
        const key = JSON.stringify([sourceFile.path,sourceFile.signature,hunk.id]);retained.add(key);
        if (!identifiers.has(key)) identifiers.set(key,nextId++);
        const row = {id:identifiers.get(key), key, displayId:hunk.id, projectReview:true,
          path:sourceFile.path,fileName:sourceFile.name,source:sourceFile.source,version:sourceFile.version,
          start:hunk.start,end:hunk.end,line:hunk.line,original:hunk.after,proposed:hunk.after,displayOriginal:hunk.before};
        if (sourceFile === file && state.source === file.source) {
          const from = editor.posFromIndex(index(file.source,hunk.start)), to = editor.posFromIndex(index(file.source,hunk.end));
          row.doc=state.doc;row.marker=editor.markText(from,to,{clearWhenEmpty:false});
          row.review = attachProofread(editor,from,to,hunk.before,hunk.after,hunk.changes,
            () => resolve(file,hunk,'keep'),
            () => { void resolve(file,hunk,'undo').catch(error=>message(error.message)); },changed,visible);
          rows.push(row);
        } else {
          row.review = {get busy(){return committing;},
            keep:()=>resolve(sourceFile,hunk,'keep').catch(error=>message(error.message)),
            undo:()=>resolve(sourceFile,hunk,'undo').catch(error=>message(error.message))};
        }
        if (order.includes(sourceFile.path) || sourceFile === file) entries.push(row);
      }
      for (const key of identifiers.keys()) if (!retained.has(key)) identifiers.delete(key);
      changed();
    }
    list.replaceChildren();
    for (const file of files) {
      const entry = document.createElement('button'); entry.type = 'button';
      entry.textContent = file.name + ' · ' + file.hunks.length;
      entry.onclick = async () => {
        popover.hidePopover();
        if (await open(file.path)) {
          await refresh();
          const first = rows[0]?.marker.find();
          if (first) editor.scrollIntoView(first.from,80);
        }
      };
      list.append(entry);
    }
    if (!visible) rows.forEach(row=>list.append(row.review.root));
  }
  async function refresh() {
    if (committing) return;
    if (loading) return loading;
    if (!enabled()) { clear(); render({enabled:false,files:[]}); changed(); return; }
    loading = (async () => {
      const result = await request('/project-review');
      if (capture().clean) render(result);
    })().catch(error => message(error.message)).finally(() => { loading = null; });
    return loading;
  }
  popover.addEventListener('beforetoggle', event => {
    if (event.newState !== 'open') return;
    popover.style.right = '8px'; popover.style.top = button.getBoundingClientRect().bottom + 6 + 'px';
  });
  editor.on('swapDoc', () => { clear(); void refresh(); });
  window.addEventListener('latex-project-review-change', () => { void savePreferences().then(refresh).catch(()=>{}); });
  window.addEventListener('latex-proofread-change', () => { signature=''; void refresh(); });
  window.addEventListener('latex-language-change', () => { signature=''; void refresh(); });
  const timer = setInterval(refresh,2000);
  window.addEventListener('pagehide',()=>clearInterval(timer));
  void Promise.resolve().then(refresh);
  return {refresh, clear, async navigate(item) {
    if (committing || !await open(item.path,false)) return false;
    await refresh();
    const row=rows.find(row=>row.key===item.key),pos=row?.marker.find();
    if (!pos) { message(t('项目修改已变化，请刷新校对后重试。')); return false; }
    editor.scrollIntoView(pos.from,80);editor.focus?.();return true;
  }, async keepAll() {
    for (const file of files.filter(item=>(order.includes(item.path)||item.path===capture().path)&&item.hunks.length)) await resolve(file,null,'keep-all');
    return true;
  }, async undoAll() {
    for (const file of files.filter(item=>(order.includes(item.path)||item.path===capture().path)&&item.hunks.length)) await resolve(file,null,'undo-all');
    return true;
  }, get items(){return rows;},get allItems(){return entries;},get sourceOrder(){return order;},get busy(){return committing;}};
}
