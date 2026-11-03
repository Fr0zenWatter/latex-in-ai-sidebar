import assert from 'node:assert/strict';
import {attachProjectReview} from './vendor/latex-project-review.mjs';

const elements=new Map(), stored=new Map([['latex-codex-proofread-project','on']]), events={};
function element(id) {
  if(!elements.has(id)) elements.set(id,{children:[],style:{},textContent:'',attributes:{},events:{},
    setAttribute(key,value){this.attributes[key]=value;},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    remove(){},hidePopover(){},getBoundingClientRect(){return {bottom:40};},
    addEventListener(key,fn){this.events[key]=fn;}});
  return elements.get(id);
}
globalThis.document={querySelector:element,createElement:()=>element(Symbol())};
globalThis.localStorage={getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value)};
globalThis.window={addEventListener:(key,fn)=>events[key]=fn};
let source='pre😀\nnew\nend\n',readonly=false,cleared=0,bookmark=0,displayRange,adopted=0,calls=[];
const doc={},wrapper={clientWidth:500,querySelector:()=>null};
const editor={on(){},getWrapperElement:()=>wrapper,getOption:()=>readonly,setOption:(_,value)=>readonly=value,
  posFromIndex:ch=>({line:0,ch}),scrollIntoView(){},
  setBookmark(from){bookmark++;return {clear(){cleared++;},changed(){}};},
  markText(from,to,options){if(options.replacedWith)displayRange={from,to};return {find:()=>({from,to}),clear(){cleared++;},changed(){}};}};
const file={path:'main.tex',name:'main.tex',source,version:'v1',signature:'s1',hunks:[{id:1,start:5,end:9,
  before:'old\n',after:'new\n',changes:[{kind:'delete',text:'old'},{kind:'insert',text:'new'},{kind:'equal',text:'\n'}]}]};
let files=[file],clean=true;
const request=async(url,options)=>{
  assert.equal(url,'/project-review');
  if(!options)return {enabled:true,files};
  const action=JSON.parse(options.body);calls.push(action);
  assert.equal(action.signature,file.signature);assert.equal(readonly,true);
  files=[];
  return {enabled:true,files,state:{source:action.action==='undo'?'pre😀\nold\nend\n':source,version:'v2',path:'main.tex'}};
};
const review=attachProjectReview({editor,request,capture:()=>({path:'main.tex',source,doc,clean}),
  adopt:async state=>{source=state.source;adopted++;},open:async()=>true,message(){}});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(review.items.length,1);assert.equal(element('#project-review-open').hidden,false);
assert.equal(displayRange.from.ch,6,'Code-point offsets convert to JavaScript UTF-16 offsets.');
assert.equal(displayRange.to.ch,10);
assert.equal(review.items[0].displayOriginal,'old\n');
clean=false;await review.items[0].review.keep();assert.equal(calls.length,0,'Unsaved drafts block both review actions.');
clean=true;await review.items[0].review.keep();assert.equal(calls[0].action,'keep');
assert.equal(source,file.source);assert.equal(review.items.length,0);assert.equal(readonly,false);
files=[file];await review.refresh();
review.items[0].review.undo();await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls.at(-1).action,'undo');assert.equal(source,'pre😀\nold\nend\n');assert.equal(adopted,2);
file.source=source;file.signature='s2';file.version='v2';
file.hunks=[{id:1,start:5,end:5,before:'deleted\n',after:'',changes:[{kind:'delete',text:'deleted\n'}]}];
files=[file];await review.refresh();assert.equal(bookmark,1,'Pure deletions have a visible review at the insertion point.');
stored.set('latex-codex-proofread-editor','off');await review.refresh();
assert(element('#project-review-list').children.includes(review.items[0].review.root),'PDF-only mode keeps source actions in the project list.');
clean=false;await assert.rejects(()=>review.keepAll(),/Save the editor draft/);
assert.equal(review.items.length,1);
clean=true;await review.keepAll();assert.equal(calls.at(-1).action,'keep-all');
assert.equal(calls.at(-1).id,undefined);assert.equal(review.items.length,0);
files=[file];await review.refresh();
clean=false;await assert.rejects(()=>review.undoAll(),/Save the editor draft/);
clean=true;await review.undoAll();assert.equal(calls.at(-1).action,'undo-all');
assert.equal(calls.at(-1).id,undefined);assert.equal(review.items.length,0);
files=[file];await review.refresh();
stored.set('latex-codex-proofread-project','off');await review.refresh();
assert.equal(review.items.length,0);assert.equal(element('#project-review-open').hidden,true);assert(cleared>0);
events.pagehide();

// Several files may use the same local hunk ID; the PDF needs unique, stable IDs.
stored.set('latex-codex-proofread-project','on');stored.set('latex-codex-proofread-editor','on');
const main={...file,source:'main new',signature:'main-v1',hunks:[{id:1,start:0,end:8,before:'main old',after:'main new'}]};
const child={...main,path:'parts/child.tex',name:'parts/child.tex',source:'child new',signature:'child-v1',version:'c1',
  hunks:[{id:1,start:0,end:9,before:'child old',after:'child new'}]};
const other={...main,path:'other.tex',name:'other.tex',signature:'other-v1'};
let active=main,activeDoc={},activeSource=main.source,pendingFiles=[main,child,other],multiCalls=[],opened=[],scrolls=[],errors=[],multiFailure=null;
const swapHandlers={};
const multiEditor={...editor,on:(key,fn)=>swapHandlers[key]=fn,scrollIntoView:pos=>scrolls.push(pos),focus(){}};
const multi=attachProjectReview({editor:multiEditor,
  capture:()=>({path:active.path,source:activeSource,version:active.version,doc:activeDoc,clean,
    mainFile:main.path,projectRoot:'project'}),
  request:async(url,options)=>{
    assert.equal(url,'/project-review');
    if(!options){await new Promise(resolve=>setImmediate(resolve));return {enabled:true,files:pendingFiles,source_order:[main.path,child.path]};}
    const action=JSON.parse(options.body);multiCalls.push(action);
    const target=pendingFiles.find(file=>file.path===action.path);
    if (target.path===multiFailure) throw new Error('Project save failed');
    assert.equal(action.current_path,active.path);assert.equal(action.current_version,active.version);
    assert.equal(action.main_file,main.path);assert.equal(action.project_root,'project');
    assert.equal(action.version,target.version);assert.equal(action.signature,target.signature);
    pendingFiles=pendingFiles.filter(file=>file!==target);
    const after=action.action.startsWith('undo')?target.hunks[0].before:target.source;
    return {enabled:true,files:pendingFiles,source_order:[main.path,child.path],
      state:{...target,source:after},
      current_state:{...active,source:target.path===active.path?after:activeSource}};
  },
  adopt:async state=>{assert.equal(state.path,active.path);activeSource=state.source;},
  open:async(path,initialize)=>{
    opened.push([path,initialize]);active=[main,child,other].find(file=>file.path===path);
    activeSource=active.source;activeDoc={};swapHandlers.swapDoc();return true;
  },message:text=>errors.push(text)});
await multi.refresh();
assert.equal(multi.items.length,1,'Source widgets and bulk actions stay in the current file.');
assert.equal(element('#project-review-list').children.length,3,'The project menu still includes independent documents.');
assert.deepEqual(multi.allItems.map(item=>item.path),[main.path,child.path],'PDF aggregation excludes unrelated documents.');
assert.equal(new Set(multi.allItems.map(item=>item.id)).size,2);
const childItem=multi.allItems.find(item=>item.path===child.path),childId=childItem.id;
await multi.refresh();assert.equal(multi.allItems.find(item=>item.path===child.path).id,childId);
assert.equal(await multi.navigate(childItem),true);assert.deepEqual(opened.at(-1),[child.path,false]);
assert.equal(multi.items[0].id,childId);assert(scrolls.length);
const mainItem=multi.allItems.find(item=>item.path===main.path);
clean=false;await mainItem.review.undo();assert.equal(multiCalls.length,0);assert(errors.length);
clean=true;await mainItem.review.undo();assert.equal(multiCalls.at(-1).path,main.path);
assert.equal(activeSource,child.source,'Undo in another file cannot replace the active editor document.');
assert.deepEqual(multi.allItems.map(item=>item.path),[child.path]);
await multi.keepAll();assert.equal(multiCalls.at(-1).path,child.path);assert.equal(multiCalls.at(-1).action,'keep-all');
assert.equal(pendingFiles[0].path,other.path,'Current-file bulk Keep leaves other documents pending.');
assert.equal(multi.allItems.length,0);
assert.equal(await multi.navigate(mainItem),false,'Navigation rejects a change that has already been resolved.');
pendingFiles=[main,child,other];active=child;activeSource=child.source;await multi.refresh();
const allKeepStart=multiCalls.length;await multi.keepAll();
assert.deepEqual(multiCalls.slice(allKeepStart).map(item=>[item.path,item.action]),[[main.path,'keep-all'],[child.path,'keep-all']]);
assert.deepEqual(pendingFiles.map(item=>item.path),[other.path],'Article-wide Keep excludes independent papers.');
pendingFiles=[main,child,other];activeSource=child.source;await multi.refresh();
const allUndoStart=multiCalls.length;await multi.undoAll();
assert.deepEqual(multiCalls.slice(allUndoStart).map(item=>[item.path,item.action]),[[main.path,'undo-all'],[child.path,'undo-all']]);
assert.equal(activeSource,'child old');assert.deepEqual(pendingFiles.map(item=>item.path),[other.path]);
pendingFiles=[main,child,other];activeSource=child.source;await multi.refresh();multiFailure=child.path;
await assert.rejects(()=>multi.keepAll(),/Project save failed/);
assert.deepEqual(pendingFiles.map(item=>item.path),[child.path,other.path]);assert.equal(multi.busy,false);assert.equal(readonly,false);
multiFailure=null;await multi.keepAll();assert.deepEqual(pendingFiles.map(item=>item.path),[other.path]);
await element('#project-review-list').children[0].onclick();
assert.deepEqual(opened.at(-1),[other.path,undefined],'Opening an independent article keeps ordinary PDF initialization.');
events.pagehide();
console.log('PASS: project widgets, Unicode, draft protection, per-file and remote Keep/Undo, stable IDs, article scope and cross-file navigation');
