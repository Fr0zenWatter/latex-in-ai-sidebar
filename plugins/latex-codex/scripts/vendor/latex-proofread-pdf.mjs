import {pdfTextRect,pdfPageBoxes} from './latex-pdf-analysis.mjs';

// Coordinates disposable PDF previews. This never invokes /save or /compile.
export function attachProofreadPdf({request, capture, display, restore, message, changed=()=>{}}) {
  let signature = '', generation = 0, timer = null, shown = null, pending = false, latest = null;
  function invalidate() {
    clearTimeout(timer); generation++; signature = ''; shown = null; pending = false; latest = null;
    changed();
  }
  function update(annotations) {
    const state = capture(), items = [], files = new Map();
    let project = false;
    if (state.latex && state.enabled !== false) for (const item of annotations) {
      if (item.path && item.path !== state.path && item.review) {
        const file=files.get(item.path)||{path:item.path,version:item.version,source:item.source,items:[]};
        file.items.push({id:item.id,start:item.start,end:item.end,selection:item.original,replacement:item.proposed,
          ...(item.projectReview?{display_original:item.displayOriginal}:{})});
        files.set(item.path,file);project=true;continue;
      }
      const pos = item.marker?.find();
      if (!item.review || !pos || item.doc !== state.doc || state.range(pos.from,pos.to) !== item.original) continue;
      project ||= !!item.projectReview;
      items.push({id:item.id, start:Array.from(state.source.slice(0,state.index(pos.from))).length,
        end:Array.from(state.source.slice(0,state.index(pos.to))).length, selection:item.original, replacement:item.proposed, ...(item.projectReview?{display_original:item.displayOriginal}:{})});
    }
    const documents = [{path:state.path,version:state.version,source:state.source,items},...files.values()]
      .sort((a,b)=>a.path.localeCompare(b.path));
    for(const file of documents)file.items.sort((a,b)=>a.start-b.start||a.end-b.end||a.id-b.id);
    // Changing the open editor does not change the article's preview inputs.
    const next = items.length||files.size ? JSON.stringify([state.projectRoot,state.mainFile,documents]) : '';
    latest = {state,items,files,project,annotations};
    if (next === signature) return;
    signature = next; const token = ++generation; clearTimeout(timer);
    const current = () => token === generation;
    if (!next) {
      pending = false;
      changed();
      if (shown) { shown = null; void restore(current).catch(error => { if(current())message('PDF 校对预览失败：{message}',{message:error.message}); }); }
      return;
    }
    pending = true;
    changed();
    timer = setTimeout(async () => {
      const {state,items,files,project} = latest;
      message('正在编译 PDF 校对预览…');
      try {
        const data = await request('/proofread',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({path:state.path,version:state.version,source:state.source,items,
            ...(project?{files:[...files.values()],main_file:state.mainFile,project_root:state.projectRoot}:{})})});
        if (!current()) return;
        shown = data;
        if (await display(data,current) === false || !current()) return;
        message(data.plain_preview ? 'PDF 校对预览：当前选区仅显示修改后内容，请在源码中对比原文。' : 'PDF 校对预览：红色删除，绿色保留；不写入历史。');
      } catch (error) {
        if (current() && error.conflict && latest.state.path !== state.path) {
          signature = ''; update(latest.annotations); return;
        }
        if (current()) {
          shown = null;
          await restore(current).catch(()=>{});
          message('PDF 校对预览失败：{message}',{message:error.message});
        }
      } finally { if(current()){pending = false;changed();} }
    },200);
  }
  return {update,invalidate,get active(){return pending || !!shown;},get data(){return shown;}};
}

export function proofreadPdfPositions(viewer, data, annotations, build) {
  if (!data || data.pdf_revision !== build) return [];
  const pending = new Set(annotations.filter(item => item.review).map(item => item.id)), positions = new Map();
  for (const region of data.regions) {
    const view = viewer.getPageView(region.page-1);
    if (!pending.has(region.id) || !view?.viewport || data.top_origin && !view.pdfPage) continue;
    const [left,,,topEdge] = data.top_origin ? view.pdfPage.mediaBox : [0,0,0,0];
    const top = view.div.offsetTop + view.viewport.convertToViewportPoint(left+region.rect[0],topEdge+region.rect[3])[1];
    positions.set(region.id, Math.min(positions.get(region.id) ?? Infinity, top));
  }
  return [...positions.values()];
}

export function proofreadHighlights(content, pending) {
  const stack = [], spans = [];
  let sequence = 0;
  for (const item of content.items) {
    if (item.type === 'beginMarkedContent' || item.type === 'beginMarkedContentProps') {
      const tag = /^LC(Before|After)(Changed)?([1-9]\d*)$/.exec(item.tag || '');
      stack.push(tag && pending.has(Number(tag[3])) ? {id:Number(tag[3]),side:tag[1].toLowerCase(),strong:!!tag[2],sequence,rect:null} : null);
    } else if (item.type === 'endMarkedContent') {
      const span = stack.pop();
      if (span?.rect) spans.push(span);
    } else if (item.str?.trim()) {
      const span = stack.findLast(value => value);
      if (!span) { sequence++; continue; }
      const rect = pdfTextRect(item, content.styles);
      if (!rect.every(Number.isFinite) || rect[2] <= rect[0] || rect[3] <= rect[1]) continue;
      span.rect = span.rect ? [Math.min(span.rect[0],rect[0]),Math.min(span.rect[1],rect[1]),
        Math.max(span.rect[2],rect[2]),Math.max(span.rect[3],rect[3])] : rect;
    }
  }
  const rows = [];
  for (const span of spans) {
    const [l,b,r,t] = span.rect;
    const row = rows.find(row => row.id === span.id && row.side === span.side &&
      Math.abs(row.baseline-(b+t)/2) < Math.max(row.height,t-b)*.7);
    if (row) row.rect = [Math.min(row.rect[0],l),Math.min(row.rect[1],b),Math.max(row.rect[2],r),Math.max(row.rect[3],t)];
    else rows.push({...span,strong:false,baseline:(b+t)/2,height:t-b,rect:[l,b,r,t]});
  }
  const strong = [];
  let previous = null;
  for (const span of spans) {
    if (span.strong) {
      const [l,b,r,t] = span.rect, height = t-b;
      const prior = previous?.rect;
      if (previous?.strong && previous.id === span.id && previous.side === span.side && previous.sequence === span.sequence &&
          Math.abs((prior[1]+prior[3]-b-t)/2) < Math.min(prior[3]-prior[1],height)*.35 &&
          l-prior[2] >= -1 && l-prior[2] <= Math.max(prior[3]-prior[1],height)*1.5) {
        const merged = strong.at(-1).rect;
        merged[0]=Math.min(merged[0],l);merged[1]=Math.min(merged[1],b);
        merged[2]=Math.max(merged[2],r);merged[3]=Math.max(merged[3],t);
      } else strong.push({...span,rect:[...span.rect]});
    }
    previous = span;
  }
  return [...rows,...strong];
}

const highlightContent = new WeakMap();
function proofreadTextContent(page) {
  if (!highlightContent.has(page)) {
    const content = page.getTextContent({includeMarkedContent:true,disableNormalization:true,disableCombineTextItems:true});
    highlightContent.set(page,content);
    content.catch(() => highlightContent.delete(page));
  }
  return highlightContent.get(page);
}

async function proofreadSourceDigest(source) {
  const digest = await crypto.subtle.digest('SHA-256',new TextEncoder().encode(source.replace(/\r\n?/g,'\n')));
  return Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,'0')).join('');
}

export async function locateProofreadSource({viewer,preview,position,capture,request,open,focus,isCurrent=()=>true}) {
  const data=preview.data,pdf=viewer.pdfDocument,state=await capture();
  const stale=()=>new Error('PDF 或源码已变化，请重新选择 PDF 文字。');
  if(!data?.proofread_version||!pdf||data.pdf_revision!==state.pdfRevision)throw stale();
  const same=(a,b)=>a.path===b.path&&a.version===b.version&&a.source===b.source&&a.doc===b.doc
    &&a.mainFile===b.mainFile&&a.projectRoot===b.projectRoot;
  const current=async()=>isCurrent()&&preview.data===data&&viewer.pdfDocument===pdf&&same(state,await capture());
  const page=await pdf.getPage(position.page);
  let marks=[];
  try{marks=proofreadHighlights(await proofreadTextContent(page),new Set(data.regions.map(region=>region.id)));}
  catch{/* SyncTeX can still locate unmarked text when glyph extraction fails. */}
  const hits=new Set(marks.filter(mark=>position.x>=mark.rect[0]-1&&position.x<=mark.rect[2]+1
    &&position.y>=mark.rect[1]-1&&position.y<=mark.rect[3]+1).map(mark=>mark.id));
  const [boxes,digest]=await Promise.all([pdfPageBoxes(pdf),proofreadSourceDigest(state.source)]);
  if(!await current())throw stale();
  const result=await request('/synctex',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    direction:'backward',path:state.path,version:state.version,source_digest:digest,
    pdf_revision:data.pdf_revision,proofread_version:data.proofread_version,boxes,...position,
    ...(hits.size===1?{annotation_id:[...hits][0]}:{}),
  })});
  if(!await current())throw stale();
  if(result.path!==state.path&&!await open(result.path))return false;
  const target=await capture();
  if(target.path!==result.path||target.version!==result.version||target.mainFile!==state.mainFile||target.projectRoot!==state.projectRoot
    ||await proofreadSourceDigest(target.source)!==result.source_digest||!isCurrent()||!same(target,await capture()))throw stale();
  const line=Math.max(1,Math.min(result.line,target.source.split('\n').length));
  focus({line:line-1,ch:0});
  return {...result,line};
}

export async function refineProofreadRegions(viewer, data) {
  if (!viewer.pdfDocument || !data?.regions?.length || data.plain_preview) return;
  const regions = data.regions.filter(region => !region.source_only);
  const pending = new Set(regions.map(region => region.id));
  try {
    const pages = await Promise.all([...new Set(regions.map(region => region.page))].map(async number => {
      const page = await viewer.pdfDocument.getPage(number), boxes = new Map();
      const [left,,,top] = data.top_origin ? page.mediaBox : [0,0,0,0];
      for (const mark of proofreadHighlights(await proofreadTextContent(page), pending)) {
        if (mark.strong) continue;
        const rect = [mark.rect[0]-left,mark.rect[1]-top,mark.rect[2]-left,mark.rect[3]-top];
        const old = boxes.get(mark.id);
        boxes.set(mark.id,old ? [Math.min(old[0],rect[0]),Math.min(old[1],rect[1]),Math.max(old[2],rect[2]),Math.max(old[3],rect[3])] : rect);
      }
      return [...boxes].map(([id,rect]) => ({id,page:number,rect}));
    }));
    const actual = new Map(), seen = new Set();
    for (const region of pages.flat()) {
      const locations = actual.get(region.id) || [];
      locations.push(region); actual.set(region.id,locations);
    }
    // SyncTeX can reuse a preceding page's line; marked glyphs identify the real change.
    data.regions = data.regions.flatMap(region => {
      if (region.source_only || !actual.has(region.id)) return [region];
      if (seen.has(region.id)) return [];
      seen.add(region.id);
      return actual.get(region.id).map(location => ({...region,...location}));
    });
  } catch { /* Keep SyncTeX controls when marked text cannot be read. */ }
}

async function paintHighlights(view, pending, root) {
  const page = view.pdfPage;
  try {
    const content = await proofreadTextContent(page);
    // A refresh/Keep/Undo detaches the old root, so late text never paints a new preview.
    if (!root.isConnected || view.pdfPage !== page) return;
    for (const {side,strong,rect} of proofreadHighlights(content,pending)) {
      const a = view.viewport.convertToViewportPoint(rect[0]-1,rect[1]-1);
      const b = view.viewport.convertToViewportPoint(rect[2]+1,rect[3]+1);
      const mark = document.createElement('span');
      mark.className = 'pdf-proofread-highlight proofread-'+side+(strong?' proofread-word':'');
      Object.assign(mark.style,{left:Math.min(a[0],b[0])+'px',top:Math.min(a[1],b[1])+'px',
        width:Math.abs(b[0]-a[0])+'px',height:Math.abs(b[1]-a[1])+'px'});
      root.append(mark);
    }
  } catch { /* The readable PDF and Keep/Undo remain available if text extraction fails. */ }
}

const regionRefinements = new WeakMap();
export function paintProofreadActions(viewer, data, annotations, build, translate) {
  if (!data || data.pdf_revision !== build) return;
  const rows = new Map(), pages = new Set(), painted = [], pdf = viewer.pdfDocument;
  const pending = new Set(annotations.filter(item => item.review).map(item => item.id));
  for (const region of [...data.regions].sort((a,b)=>a.page-b.page || b.rect[3]-a.rect[3])) {
    const item = annotations.find(item=>item.id===region.id && item.review);
    const view = viewer.getPageView(region.page-1);
    if (!item || !view?.viewport) continue;
    if (data.top_origin && !view.pdfPage) continue;
    if (!pages.has(region.page) && view.pdfPage?.getTextContent) {
      pages.add(region.page);
      const root = document.createElement('div');root.className = 'pdf-proofread-highlights';
      root.setAttribute('aria-hidden','true');view.div.append(root);
      painted.push(root);
      void paintHighlights(view,pending,root);
    }
    const [left,,,topEdge] = data.top_origin ? view.pdfPage.mediaBox : [0,0,0,0];
    const point = view.viewport.convertToViewportPoint(left+region.rect[0],topEdge+region.rect[3]);
    let top = Math.max(4,point[1]-4);
    const occupied = rows.get(region.page) || [];
    const fileName=item.fileName||item.path?.split(/[\\/]/).pop(),height=62;
    while (occupied.some(row=>Math.abs(row.top-top)<Math.max(row.height,height))) top += height;
    occupied.push({top,height}); rows.set(region.page,occupied);
    const identifier = item.displayId ?? item.id;
    const group = document.createElement('div'); group.className = 'pdf-proofread-actions';
    group.setAttribute('role','group'); group.setAttribute('aria-label',translate('校对修改')+(fileName?' · '+fileName:'')+' #'+identifier);
    group.style.left = '4px'; group.style.top = top+'px';
    const number = document.createElement('span'); number.textContent = '#'+identifier;
    const undo = document.createElement('button'), keep = document.createElement('button');
    undo.type = keep.type = 'button'; undo.textContent = 'Undo'; keep.textContent = 'Keep';
    undo.title = translate('撤销这处建议'); keep.title = translate('保留这处修改');
    undo.setAttribute('aria-label','Undo #'+identifier+(fileName?' · '+fileName:'')); keep.setAttribute('aria-label','Keep #'+identifier+(fileName?' · '+fileName:''));
    keep.className = 'proofread-keep'; undo.disabled = keep.disabled = item.review.busy;
    undo.onclick = ()=>item.review.undo(); keep.onclick = ()=>item.review.keep();
    group.append(number,undo,keep); view.div.append(group); painted.push(group);
  }
  if (pdf && !data.plain_preview) {
    if (!regionRefinements.has(data)) {
      regionRefinements.set(data,refineProofreadRegions(viewer,data).then(() => regionRefinements.set(data,null)));
    }
    return regionRefinements.get(data)?.then(() => {
      if (viewer.pdfDocument !== pdf || !painted.some(node => node.isConnected)) return;
      painted.forEach(node => node.remove());
      paintProofreadActions(viewer,data,annotations,build,translate);
      viewer.update?.();
    });
  }
}
