// Run: node test_proofread_pdf.mjs (stdlib only).
import assert from 'node:assert/strict';
import {attachProofreadPdf,paintProofreadActions,proofreadPdfPositions,proofreadHighlights,refineProofreadRegions,locateProofreadSource} from './vendor/latex-proofread-pdf.mjs';
import {createHash,webcrypto} from 'node:crypto';
if(!globalThis.crypto)globalThis.crypto=webcrypto;
let timers=new Map(),timerId=0;
globalThis.setTimeout=fn=>{timers.set(++timerId,fn);return timerId;};globalThis.clearTimeout=id=>timers.delete(id);
const flush=async()=>{const work=[...timers.values()];timers.clear();await Promise.all(work.map(fn=>fn()));};
const doc={},source='😀 old text';
let current={latex:true,path:'main.tex',version:'v1',source,doc,range:()=> 'old',index:pos=>pos.ch};
const annotation={id:1,doc,original:'old',proposed:'new',review:{},marker:{find:()=>({from:{ch:3},to:{ch:6}})}};
let release,calls=[],displayed=[],restores=0,messages=[];
const preview=attachProofreadPdf({capture:()=>current,request:async(route,options)=>{
  calls.push({route,data:JSON.parse(options.body)});return new Promise(resolve=>release=resolve);
},display:async data=>displayed.push(data),restore:async()=>{restores++;},message:key=>messages.push(key)});
preview.update([annotation]);preview.update([annotation]);assert.equal(timers.size,1);
const pending=flush();assert.equal(calls.length,1);assert.equal(calls[0].route,'/proofread');
assert.equal(calls[0].data.items[0].start,2,'Wire ranges must use Unicode code points.');
preview.update([]);release({pdf_revision:'stale',regions:[]});await pending;
assert.equal(displayed.length,0);assert.equal(preview.active,false);
preview.update([annotation]);const next=flush();release({pdf_revision:'shown',regions:[]});await next;
assert.equal(displayed.length,1);assert.equal(preview.active,true);assert.equal(preview.data.pdf_revision,'shown');
preview.update([]);await Promise.resolve();assert.equal(restores,1);assert.equal(preview.active,false);
current={...current,enabled:false};preview.update([annotation]);await flush();assert.equal(calls.length,2,'Disabled PDF proofreading never compiles.');
current={...current,enabled:true};preview.update([annotation]);const toggledPending=flush();
current={...current,enabled:false};preview.update([annotation]);release({pdf_revision:'disabled-stale',regions:[]});await toggledPending;
assert.equal(displayed.length,1,'Turning off while compiling suppresses the old preview.');
current={...current,enabled:true};preview.update([annotation]);const visibleAgain=flush();release({pdf_revision:'enabled',regions:[]});await visibleAgain;
current={...current,enabled:false};preview.update([annotation]);await Promise.resolve();assert.equal(restores,2);assert.equal(preview.active,false);
current={...current,enabled:true};
current={...current,latex:false};preview.update([annotation]);assert.equal(timers.size,0);
current={...current,latex:true,range:()=> 'edited'};preview.update([annotation]);assert.equal(timers.size,0);
let failedRestore=0;
const failure=attachProofreadPdf({capture:()=>({...current,range:()=> 'old'}),request:async()=>{throw new Error('Bad TeX');},
  display:async()=>assert.fail(),restore:async()=>failedRestore++,message:key=>messages.push(key)});
failure.update([annotation]);await flush();assert.equal(failedRestore,1);assert.equal(failure.active,false);
assert(messages.includes('PDF 校对预览失败：{message}'));
const plain=attachProofreadPdf({capture:()=>({...current,range:()=> 'old'}),request:async()=>({pdf_revision:'plain',plain_preview:true,regions:[]}),
  display:async()=>true,restore:async()=>{},message:key=>messages.push(key)});
plain.update([annotation]);await flush();
assert.equal(plain.active,true);
assert(messages.includes('PDF 校对预览：当前选区仅显示修改后内容，请在源码中对比原文。'),'Plain fallback must be visible to the reader.');

// Opening an unchanged child still previews every pending file in its article.
const projectState={latex:true,path:'child.tex',version:'child-v1',source:'Unchanged child.',doc:{},
  mainFile:'main.tex',projectRoot:'/project',range:()=>'',index:()=>0};
const remote={id:100001,displayId:1,projectReview:true,path:'main.tex',fileName:'main.tex',version:'main-v1',
  source:'😀 main new',start:2,end:10,original:'main new',proposed:'main new',displayOriginal:'main old',review:{}};
let projectPayload,projectCompiles=0;
const project=attachProofreadPdf({capture:()=>projectState,request:async(route,options)=>{
  assert.equal(route,'/proofread');projectCompiles++;projectPayload=JSON.parse(options.body);
  return {pdf_revision:'combined',regions:[]};
},display:async()=>true,restore:async()=>{},message(){}});
project.update([remote]);await flush();
assert.equal(project.active,true);assert.deepEqual(projectPayload.items,[]);
assert.equal(projectPayload.main_file,'main.tex');assert.equal(projectPayload.project_root,'/project');
assert.deepEqual(projectPayload.files,[{path:'main.tex',version:'main-v1',source:'😀 main new',items:[
  {id:100001,start:2,end:10,selection:'main new',replacement:'main new',display_original:'main old'}]}]);
project.update([remote]);await flush();assert.equal(projectCompiles,1);
project.update([{...remote,version:'main-v2'}]);await flush();assert.equal(projectCompiles,2,'Inactive versions participate in preview invalidation.');
project.update([{...remote,projectReview:false,proposed:'main improved'}]);await flush();
assert.deepEqual(projectPayload.files[0].items,[{id:100001,start:2,end:10,selection:'main new',replacement:'main improved'}],
  'Inactive internal AI reviews compile alongside the active source without a project-review baseline.');
const localProject={...annotation,id:100002,projectReview:true,path:current.path,displayOriginal:'previous'};
const localPayload=attachProofreadPdf({capture:()=>({...current,range:()=> 'old'}),request:async(route,options)=>{
  const data=JSON.parse(options.body);assert.deepEqual(data.files,[]);assert.equal(data.items[0].display_original,'previous');
  return {pdf_revision:'local-project',regions:[]};
},display:async()=>true,restore:async()=>{},message(){}});
localPayload.update([localProject]);await flush();

// Moving between the same article's reviewed files must retain the compiled PDF.
const articleA={path:'/article/a.tex',version:'a-v1',source:'😀 old A.\nTail A.',doc:{}},
  articleB={path:'/article/b.tex',version:'b-v1',source:'old B.\nTail B.',doc:{}};
const localA={id:1,path:articleA.path,doc:articleA.doc,original:'old A.',proposed:'new A.',review:{},
  marker:{find:()=>({from:{ch:3},to:{ch:9}})}};
const localB={id:2,path:articleB.path,doc:articleB.doc,original:'old B.',proposed:'new B.',review:{},projectReview:true,displayOriginal:'baseline B.',
  marker:{find:()=>({from:{ch:0},to:{ch:6}})}};
const remoteA={...localA,version:articleA.version,source:articleA.source,start:2,end:8},
  remoteB={...localB,version:articleB.version,source:articleB.source,start:0,end:6};
let activeArticle=articleA,articleMain='/article/main.tex',articleRoot='/article',holdArticle=true,releaseArticle;
const articleRequests=[],articleDisplays=[],articleMessages=[];
const article=attachProofreadPdf({capture:()=>({...activeArticle,latex:true,mainFile:articleMain,projectRoot:articleRoot,
  range:(from,to)=>activeArticle.source.slice(from.ch,to.ch),index:pos=>pos.ch}),
  request:async(route,options)=>{assert.equal(route,'/proofread');articleRequests.push(JSON.parse(options.body));
    return holdArticle?new Promise(resolve=>releaseArticle=resolve):{pdf_revision:'article-'+articleRequests.length,regions:[]};},
  display:async data=>articleDisplays.push(data),restore:async()=>assert.fail('Navigation cannot restore or replace the preview.'),
  message:key=>articleMessages.push(key),
});
article.update([remoteB,localA]);const articleTimer=[...timers.keys()][0];
activeArticle=articleB;article.update([localB,remoteA]);
assert.deepEqual([...timers.keys()],[articleTimer],'A source switch retains the existing debounce.');
const articleWaiting=flush();
assert.equal(articleRequests.length,1);assert.equal(articleRequests[0].path,articleB.path,'The pending request follows the latest active file.');
assert.equal(articleRequests[0].items[0].id,2);assert.equal(articleRequests[0].files[0].items[0].id,1);
releaseArticle({pdf_revision:'article-first',regions:[]});await articleWaiting;
const articlePdf=article.data,articleMessageCount=articleMessages.length;
activeArticle=articleA;article.update([localA,remoteB]);await flush();
activeArticle=articleB;article.update([remoteA,localB]);await flush();
assert.equal(articleRequests.length,1,'Different active files and annotation order do not recompile the same article.');
assert.strictEqual(article.data,articlePdf);assert.equal(articleDisplays.length,1);
assert.equal(articleMessages.length,articleMessageCount,'Pure navigation never flashes the compile message.');
localA.proposed=remoteA.proposed='better A.';article.update([localB,remoteA]);const articleInFlight=flush();
activeArticle=articleA;article.update([remoteB,localA]);
assert.equal(timers.size,0,'Switching files during a real preview build cannot enqueue a duplicate.');
releaseArticle({pdf_revision:'article-updated',regions:[]});await articleInFlight;
assert.equal(articleRequests.length,2);assert.equal(articleDisplays.length,2);assert.equal(article.data.pdf_revision,'article-updated');
holdArticle=false;
articleA.source=remoteA.source='😀 old A.\nChanged tail A.';articleA.version=remoteA.version='a-v2';
article.update([localA,remoteB]);await flush();assert.equal(articleRequests.length,3,'Real source/version changes still rebuild.');
activeArticle=articleB;article.update([remoteA,localB]);await flush();assert.equal(articleRequests.length,3);
articleB.source='old B.\nManual draft B.';article.update([localB,remoteA]);await flush();assert.equal(articleRequests.length,4,'Unsaved edits outside review ranges remain part of the preview.');
article.update([localB]);await flush();assert.equal(articleRequests.length,5,'Resolving a suggestion changes the preview.');
article.invalidate();article.update([localB]);await flush();assert.equal(articleRequests.length,6,'Explicit invalidation still rebuilds.');
articleMain='/article/other.tex';article.update([localB]);await flush();assert.equal(articleRequests.length,7);
articleRoot='/other-project';article.update([localB]);await flush();assert.equal(articleRequests.length,8,'Project context remains part of the preview key.');
activeArticle=articleA;
let rejectSwitched,resolveSwitched;const switchedRequests=[];
const switched=attachProofreadPdf({capture:()=>({...activeArticle,latex:true,mainFile:articleMain,projectRoot:articleRoot,
  range:(from,to)=>activeArticle.source.slice(from.ch,to.ch),index:pos=>pos.ch}),
  request:async(route,options)=>{switchedRequests.push(JSON.parse(options.body));return new Promise((resolve,reject)=>{resolveSwitched=resolve;rejectSwitched=reject;});},
  display:async()=>true,restore:async()=>assert.fail('An outdated request context is retried without restoring the PDF.'),message(){},
});
switched.update([localA,remoteB]);const outdatedContext=flush();
activeArticle=articleB;switched.update([remoteA,localB]);
rejectSwitched(Object.assign(new Error('Active source changed before the request arrived.'),{conflict:true}));await outdatedContext;
assert.equal(timers.size,1,'A rejected request for the previous active file follows the latest source context.');
const latestContext=flush();assert.equal(switchedRequests[1].path,articleB.path);
resolveSwitched({pdf_revision:'retargeted',regions:[]});await latestContext;
assert.equal(switched.data.pdf_revision,'retargeted');assert.equal(timers.size,0);

const element=()=>({children:[],style:{},attributes:{},append(...nodes){this.children.push(...nodes);},setAttribute(key,value){this.attributes[key]=value;}});
globalThis.document={createElement:element};
let kept=0,undone=0;annotation.review={busy:false,keep:()=>kept++,undo:()=>undone++};
const page=element(),viewer={getPageView:()=>({div:page,viewport:{convertToViewportPoint:(x,y)=>[x,800-y]}})};
paintProofreadActions(viewer,{pdf_revision:'shown',regions:[{id:1,page:1,rect:[70,600,300,620]}]},[annotation],'shown',key=>key);
assert.equal(page.children.length,1);const [,undo,keep]=page.children[0].children;undo.onclick();keep.onclick();
assert.equal(kept,1);assert.equal(undone,1);assert.equal(keep.attributes['aria-label'],'Keep #1');
annotation.review.busy=true;paintProofreadActions(viewer,{pdf_revision:'shown',regions:[{id:1,page:1,rect:[70,600,300,620]}]},[annotation],'shown',key=>key);
assert(page.children[1].children[2].disabled);
const namedPage=element();let remoteKept=0,remoteUndone=0;
const named={...remote,review:{keep:()=>remoteKept++,undo:()=>remoteUndone++}};
const namedChild={...named,id:100002,path:'parts/child.tex',fileName:'parts/child.tex',review:{keep:()=>remoteKept+=10,undo:()=>remoteUndone+=10}};
paintProofreadActions({getPageView:()=>({div:namedPage,viewport:{convertToViewportPoint:(x,y)=>[x,800-y]}})},
  {pdf_revision:'shown',regions:[{id:named.id,page:1,rect:[70,600,300,620]},{id:namedChild.id,page:1,rect:[70,600,300,620]}]},
  [named,namedChild],'shown',key=>key);
assert.deepEqual(namedPage.children.map(group=>group.children[0].textContent),['#1','#1']);
assert(namedPage.children.every(group=>group.children.length===3),'File paths do not widen the compact number/Undo/Keep controls.');
assert.equal(namedPage.children[1].children[1].attributes['aria-label'],'Undo #1 · parts/child.tex');
assert.equal(Number.parseFloat(namedPage.children[1].style.top)-Number.parseFloat(namedPage.children[0].style.top),62,'Named files use the same compact spacing.');
namedPage.children[1].children[1].onclick();namedPage.children[0].children[2].onclick();
assert.equal(remoteUndone,10);assert.equal(remoteKept,1,'Each named control calls the correct file action, even with matching display IDs.');
const orderedPage=element();
paintProofreadActions({getPageView:()=>({div:orderedPage,viewport:{convertToViewportPoint:(x,y)=>[x,800-y]}})},
  {pdf_revision:'shown',regions:[{id:namedChild.id,page:1,rect:[70,560,300,580]},{id:named.id,page:1,rect:[70,600,300,620]}]},
  [namedChild,named],'shown',key=>key);
assert.deepEqual(orderedPage.children.map(group=>group.attributes['aria-label']),['校对修改 · main.tex #1','校对修改 · parts/child.tex #1'],'Stack compact controls in PDF reading order, independent of the active source file.');
page.offsetTop=200;
const regions={pdf_revision:'shown',regions:[{id:1,page:1,rect:[70,600,300,620]},
  {id:1,page:2,rect:[70,400,300,420]},{id:2,page:1,rect:[70,200,300,220]}]};
assert.deepEqual(proofreadPdfPositions(viewer,regions,[annotation],'shown'),[380], 'Cross-page regions count as one suggestion at its first occurrence.');
assert.deepEqual(proofreadPdfPositions(viewer,regions,[],'shown'),[], 'Keep/Undo removes resolved targets.');
assert.deepEqual(proofreadPdfPositions(viewer,regions,[annotation],'other'),[], 'Stale PDF geometry cannot navigate.');
const croppedViewer={getPageView:()=>({div:page,pdfPage:{mediaBox:[10,20,610,820]},viewport:{convertToViewportPoint:(x,y)=>[x,820-y]}})};
assert.deepEqual(proofreadPdfPositions(croppedViewer,{pdf_revision:'shown',top_origin:true,regions:[{id:1,page:1,rect:[70,-220,300,-200]}]},[annotation],'shown'),[400]);
const glyph=(str,x,y=100)=>({str,transform:[10,0,0,10,x,y],width:10,height:10,fontName:'font'});
const begin=tag=>({type:'beginMarkedContent',tag}),end={type:'endMarkedContent'};
const content={styles:{font:{ascent:.8,descent:-.2}},items:[
  glyph('outside',0),begin('LCBefore1'),glyph('same',20),end,
  begin('LCBeforeChanged1'),glyph('old',35),end,
  begin('LCAfterChanged1'),glyph('new',20,80),end,
  begin('Artifact'),glyph('ignored',300),end,
  begin('LCBeforeChanged2'),glyph('resolved',300),end]};
const backgrounds=proofreadHighlights(content,new Set([1]));
assert.equal(backgrounds.length,4,'Two pale rows and two deeper word backgrounds.');
assert.deepEqual(backgrounds.filter(mark=>mark.strong).map(mark=>mark.side),['before','after']);
assert.deepEqual(backgrounds[0].rect,[20,98,45,108],'Join unchanged and changed words into one pale row.');
assert.deepEqual(proofreadHighlights(content,new Set()),[],'Resolved annotations leave no backgrounds.');
const changedWord=(word,x,y=100,tag='LCBeforeChanged1')=>[begin(tag),glyph(word,x,y),end];
const joined={styles:content.styles,items:[...changedWord('first',0),glyph(' ',10),...changedWord('second',15),...changedWord('third',30)]};
assert.deepEqual(proofreadHighlights(joined,new Set([1])).filter(mark=>mark.strong).map(mark=>mark.rect),[[0,98,40,108]],'Adjacent changed words share one continuous background across spaces.');
for(const separator of [[begin('LCBefore1'),glyph('same',15),end],[glyph('untagged',15)]]){
  const separated={styles:content.styles,items:[...changedWord('first',0),...separator,...changedWord('last',30)]};
  assert.equal(proofreadHighlights(separated,new Set([1])).filter(mark=>mark.strong).length,2,'Unchanged or untagged visible text breaks a deep background.');
}
for(const next of [changedWord('nextline',0,80),changedWord('nextside',15,100,'LCAfterChanged1'),changedWord('nextitem',15,100,'LCBeforeChanged2'),changedWord('nextcolumn',100)]){
  assert.equal(proofreadHighlights({styles:content.styles,items:[...changedWord('first',0),...next]},new Set([1,2])).filter(mark=>mark.strong).length,2,'Do not bridge lines, sides, suggestions or distant columns.');
}
const markedPage=items=>({mediaBox:[10,20,610,820],getTextContent:async()=>({styles:content.styles,items})});
const markedPages=new Map([
  [20,markedPage(changedWord('cross-page',15,140,'LCBeforeChanged6'))],
  [21,markedPage([...changedWord('fifth',35,100,'LCBeforeChanged5'),...changedWord('new',35,80,'LCAfterChanged5'),
    ...changedWord('cross-page',15,140,'LCAfterChanged6')])],
]);
const unmarked={id:7,path:'macro.tex',page:20,rect:[1,-50,60,-20]},sourceOnly={id:8,page:1,rect:[0,-20,100,-4],source_only:true};
const corrected={top_origin:true,regions:[
  {id:5,path:'methods.tex',page:20,rect:[1,-50,60,-20]},{id:5,path:'methods.tex',page:21,rect:[1,-150,60,-120]},
  {id:6,page:20,rect:[1,-50,60,-20]},{id:6,page:21,rect:[1,-50,60,-20]},unmarked,sourceOnly,
]};
const readPages=[];
await refineProofreadRegions({pdfDocument:{getPage:async number=>{readPages.push(number);return markedPages.get(number);}}},corrected);
assert.deepEqual(readPages,[20,21],'Inspect candidate pages once, excluding source-only controls.');
assert.deepEqual(corrected.regions.filter(region=>region.id===5),[{id:5,path:'methods.tex',page:21,rect:[25,-742,35,-712]}],
  'A borrowed SyncTeX line on page 20 must not duplicate the fifth change actually marked on page 21.');
assert.deepEqual(corrected.regions.filter(region=>region.id===6).map(region=>region.page),[20,21],
  'Real cross-page changes retain controls on every marked page.');
assert(corrected.regions.includes(unmarked)&&corrected.regions.includes(sourceOnly),'Unmarked macros and preamble controls retain their fallback.');
const unreadable={regions:[unmarked]},originalRegions=unreadable.regions;
await refineProofreadRegions({pdfDocument:{getPage:async()=>{throw new Error('Unreadable PDF');}}},unreadable);
assert.strictEqual(unreadable.regions,originalRegions,'Extraction failures preserve usable SyncTeX controls.');
await refineProofreadRegions({pdfDocument:{getPage:()=>assert.fail('Plain previews have no marked text.')}},{plain_preview:true,regions:[unmarked]});
const connectedElement=()=>{
  const node=element();node.isConnected=true;
  node.append=function(...nodes){this.children.push(...nodes);for(const child of nodes)child.parent=this;};
  node.remove=function(){this.isConnected=false;if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);};
  return node;
};
document.createElement=connectedElement;
const actionPages=new Map([20,21].map(number=>[number,{div:connectedElement(),pdfPage:markedPages.get(number),
  viewport:{convertToViewportPoint:(x,y)=>[x,820-y]}}]));
let updated=0;
const actionViewer={pdfDocument:{getPage:async number=>markedPages.get(number)},getPageView:index=>actionPages.get(index+1),update:()=>updated++};
const fifth={id:5,review:{}},duplicateData=()=>({pdf_revision:'shown',top_origin:true,regions:[
  {id:5,page:20,rect:[1,-50,60,-20]},{id:5,page:21,rect:[1,-150,60,-120]},
]});
await paintProofreadActions(actionViewer,duplicateData(),[fifth],'shown',key=>key);
assert.equal(actionPages.get(20).div.children.filter(node=>node.className==='pdf-proofread-actions').length,0,
  'The painter removes the phantom control without reloading or changing annotations.');
assert.equal(actionPages.get(21).div.children.filter(node=>node.className==='pdf-proofread-actions').length,1);
assert.equal(updated,1,'Refresh change navigation after correcting the PDF regions.');
for(const view of actionPages.values())for(const node of [...view.div.children])node.remove();
const stalePaint=paintProofreadActions(actionViewer,duplicateData(),[fifth],'shown',key=>key);
actionViewer.pdfDocument={};
for(const view of actionPages.values())for(const node of [...view.div.children])node.remove();
await stalePaint;
assert.equal(updated,1);assert([...actionPages.values()].every(view=>view.div.children.length===0),
  'Late region refinement cannot repaint controls after replacing the PDF or removing the preview.');
document.createElement=element;
const navSource='Original line.\nTail.',navChild='Child line.\nTail child.',navDoc={},navChildDoc={};
const navDigest=text=>createHash('sha256').update(text).digest('hex');
const navPage={mediaBox:[0,0,600,800],getTextContent:async()=>content};
const navViewer={pdfDocument:{numPages:1,getPage:async()=>navPage}};
const navPreview={data:{pdf_revision:'nav-pdf',proofread_version:'nav-proofread',regions:[{id:1,page:1,rect:[20,98,45,108]}]}};
const originalNavState={path:'main.tex',version:'v1',source:navSource,doc:navDoc,mainFile:'main.tex',projectRoot:'/project',pdfRevision:'nav-pdf'};
let navState={...originalNavState},navPayload,navFocus,navOpens=0;
const navConfig={viewer:navViewer,preview:navPreview,position:{page:1,x:25,y:100},capture:()=>navState,
  request:async(route,options)=>{
    assert.equal(route,'/synctex');navPayload=JSON.parse(options.body);
    return {path:'child.tex',version:'child-v1',source_digest:navDigest(navChild),line:2};
  },open:async path=>{navOpens++;assert.equal(path,'child.tex');navState={...navState,path,version:'child-v1',source:navChild,doc:navChildDoc};return true;},
  focus:pos=>navFocus=pos,
};
await locateProofreadSource(navConfig);
assert.equal(navPayload.annotation_id,1,'Clicks on either marked side locate the original selected source.');
assert.equal(navPayload.proofread_version,'nav-proofread');assert.equal(navPayload.source_digest,navDigest(navSource));
assert.deepEqual(navFocus,{line:1,ch:0});assert.equal(navState.doc,navChildDoc);assert.equal(navOpens,1);
navState={...originalNavState};navFocus=null;
await locateProofreadSource({...navConfig,position:{page:1,x:150,y:200}});
assert.equal(navPayload.annotation_id,undefined,'Unmodified text uses the disposable preview SyncTeX.');
navState={...originalNavState};
await locateProofreadSource({...navConfig,viewer:{pdfDocument:{numPages:1,getPage:async()=>({mediaBox:[0,0,600,800],getTextContent:async()=>{throw new Error('Text unavailable');}})}}});
assert.equal(navPayload.annotation_id,undefined,'Missing glyph text still allows preview SyncTeX navigation.');
navState={...originalNavState};navFocus=null;
assert.equal(await locateProofreadSource({...navConfig,open:async()=>false}),false);assert.equal(navFocus,null,'Cancelled source switches never report success.');
navState={...originalNavState};navFocus=null;
await assert.rejects(locateProofreadSource({...navConfig,open:async()=>{navState={...navState,path:'child.tex',version:'child-v1',source:'Manual edit',doc:navChildDoc};return true;}}),/已变化/);
assert.equal(navFocus,null,'A retained target draft that differs from the preview cannot receive a stale cursor.');
navState={...originalNavState};navFocus=null;let navRelease,navStarted;
const navStart=new Promise(resolve=>navStarted=resolve);
const delayedNavigation=locateProofreadSource({...navConfig,request:async()=>{navStarted();return new Promise(resolve=>navRelease=resolve);}});
await navStart;const oldNavData=navPreview.data;navPreview.data={...oldNavData,pdf_revision:'replacement'};
navRelease({path:'child.tex',version:'child-v1',source_digest:navDigest(navChild),line:2});
await assert.rejects(delayedNavigation,/已变化/);assert.equal(navFocus,null);navPreview.data=oldNavData;
const asyncPage=element();let releaseContent;
const pdfPage={getTextContent:()=>new Promise(resolve=>releaseContent=resolve)};
const asyncView={div:asyncPage,pdfPage,viewport:{convertToViewportPoint:(x,y)=>[x,800-y]}};
paintProofreadActions({getPageView:()=>asyncView},{pdf_revision:'shown',regions:[{id:1,page:1,rect:[20,80,45,108]}]},[annotation],'shown',key=>key);
const detached=asyncPage.children[0];detached.isConnected=false;
releaseContent(content);await Promise.resolve();await Promise.resolve();
assert.equal(detached.children.length,0,'Late text extraction must not paint a detached preview.');
console.log('PASS: disposable-only requests, Unicode ranges, deduplication, stale response exclusion, restore, errors and independent PDF controls');
