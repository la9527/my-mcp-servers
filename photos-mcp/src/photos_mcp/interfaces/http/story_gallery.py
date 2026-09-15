"""Photo-first Story galleries shared by owner pages and the private viewer.

The module intentionally exports plain CSS and JavaScript strings.  All Story
surfaces can therefore serve the same implementation without a build-time web
toolchain or a second gallery dependency.
"""

GALLERY_CSS = r"""
.psg-gallery{box-sizing:border-box;min-width:0;position:relative}
.psg-gallery *{box-sizing:border-box}
.psg-gallery[hidden]{display:none!important}
.psg-gallery button.psg-photo{appearance:none;-webkit-appearance:none;display:block;position:relative;margin:0;padding:0!important;border:0;border-radius:0!important;min-width:0!important;min-height:0!important;color:inherit;background:transparent;cursor:zoom-in;overflow:hidden}
.psg-gallery button.psg-photo:focus-visible{outline:3px solid #2f7df4;outline-offset:3px;z-index:2}
.psg-gallery .psg-photo img{display:block;width:100%;height:100%;margin:0;border-radius:0!important;object-fit:cover;pointer-events:none;user-select:none;-webkit-user-drag:none}
.psg-gallery .psg-photo.psg-load-error img{opacity:0}
.psg-photo-error{position:absolute;inset:0;display:grid;place-items:center;padding:8px;background:#222327;color:#e7e8eb;text-align:center;pointer-events:none;font:600 .72rem/1.35 system-ui,-apple-system,sans-serif;word-break:keep-all}
.psg-clusters .psg-photo-error{background:#ececee;color:#4f5158}
.psg-quiet-strip .psg-photo-error{padding:0;font-size:0}.psg-quiet-strip .psg-photo-error::before{content:"!";font-size:.75rem}
.psg-gallery.psg-reduce-motion *{scroll-behavior:auto!important;transition:none!important;animation:none!important}
.psg-empty{margin:0;padding:48px 20px;color:#85878d;text-align:center;font:500 .9rem/1.7 system-ui,-apple-system,sans-serif}

/* Quiet Memories is a calm, independent Story surface. */
.psg-quiet{width:min(1180px,100%);margin:0 auto;padding:clamp(18px,4vw,54px);background:#101113;color:#f2f2f3;font-family:system-ui,-apple-system,sans-serif}
.psg-quiet-head{display:grid;gap:7px;margin:0 0 clamp(22px,4vw,40px)}
.psg-quiet-head h2{max-width:24ch;margin:0;color:inherit;font:500 clamp(1.45rem,3vw,2.5rem)/1.25 ui-serif,"AppleMyungjo","Batang",Georgia,serif;letter-spacing:-.035em;word-break:keep-all}
.psg-quiet-head p{max-width:60ch;margin:0;color:#aeb0b5;font-size:.88rem;line-height:1.75;word-break:keep-all}
.psg-quiet-stage{display:grid;justify-items:center;gap:15px;margin:0}
.psg-quiet-main{width:100%;height:min(64dvh,680px);background:#090a0b!important;cursor:zoom-in}
.psg-quiet-main img{object-fit:contain!important}
.psg-quiet-caption{display:grid;gap:4px;justify-items:center;max-width:66ch;text-align:center}
.psg-quiet-caption strong{font:500 1rem/1.55 ui-serif,"AppleMyungjo","Batang",Georgia,serif;word-break:keep-all}
.psg-quiet-caption span{color:#aeb0b5;font-size:.78rem;line-height:1.6;word-break:keep-all}
.psg-quiet-strip{display:flex;width:100%;gap:7px;margin-top:7px;padding:5px 2px 10px;overflow-x:auto;overscroll-behavior-x:contain;scrollbar-width:thin;scroll-snap-type:x proximity}
.psg-quiet-strip .psg-photo{flex:0 0 auto;height:74px;border:2px solid transparent!important;opacity:.58;cursor:pointer;scroll-snap-align:center;transition:opacity .16s ease,border-color .16s ease}
.psg-quiet-strip .psg-photo[aria-current=true]{border-color:#f2f2f3!important;opacity:1}
.psg-quiet-strip .psg-photo:hover{opacity:.9}
.psg-quiet-position{color:#aeb0b5;font-size:.75rem;font-variant-numeric:tabular-nums;text-align:center}
.psg-quiet.psg-compact{padding:16px 12px 24px}.psg-quiet.psg-compact .psg-quiet-main{height:min(52dvh,520px)}.psg-quiet.psg-compact .psg-quiet-strip .psg-photo{height:54px}

/* Moment Clusters keeps chapter prose and photos together in an editorial grid. */
.psg-clusters{width:min(1320px,100%);margin:0 auto;padding:clamp(20px,4vw,58px);background:#fbfbfa;color:#242528;font-family:system-ui,-apple-system,sans-serif}
.psg-clusters-head{display:grid;gap:8px;margin:0 0 clamp(42px,7vw,82px)}
.psg-clusters-head h2{max-width:24ch;margin:0;font:500 clamp(1.8rem,4.2vw,3.7rem)/1.12 ui-serif,"AppleMyungjo","Batang",Georgia,serif;letter-spacing:-.045em;word-break:keep-all}
.psg-clusters-head p{max-width:60ch;margin:0;color:#717279;font-size:.9rem;line-height:1.8;word-break:keep-all}
.psg-cluster{margin:0 0 clamp(54px,8vw,108px)}
.psg-cluster-header{display:flex;align-items:baseline;gap:14px;margin:0 0 18px;border-bottom:1px solid #dedee0;padding:0 0 13px}
.psg-cluster-order{flex:0 0 auto;color:#6e7076;font:500 .82rem/1 ui-monospace,SFMono-Regular,monospace;font-variant-numeric:tabular-nums}
.psg-cluster-copy{display:grid;grid-template-columns:minmax(0,auto) minmax(12ch,1fr);align-items:baseline;gap:12px;min-width:0}
.psg-cluster-copy h3{margin:0;font:500 clamp(1.05rem,2vw,1.45rem)/1.35 ui-serif,"AppleMyungjo","Batang",Georgia,serif;word-break:keep-all}
.psg-cluster-copy p{max-width:54ch;margin:0;color:#686a70;font-size:.78rem;line-height:1.65;word-break:keep-all}
.psg-cluster-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:clamp(7px,1vw,13px);align-items:start}
.psg-cluster-grid .psg-photo{grid-column:span 4;width:100%;position:relative;background:#ececee}
.psg-cluster-grid .psg-photo.psg-feature{grid-column:span 7}
.psg-cluster-grid .psg-photo.psg-wide{grid-column:span 6}
.psg-cluster-grid .psg-photo.psg-portrait{grid-column:span 3}
.psg-cluster-grid .psg-photo.psg-feature.psg-wide{grid-column:span 7}
.psg-cluster-grid .psg-photo.psg-feature.psg-portrait{grid-column:span 4}
.psg-cluster-grid .psg-photo::after{content:"";position:absolute;inset:0;border:1px solid rgba(20,21,24,.08);pointer-events:none}
.psg-cluster-grid .psg-photo:hover img{filter:brightness(.94)}
.psg-clusters.psg-compact{padding:22px 12px 36px}.psg-clusters.psg-compact .psg-cluster-grid{grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}
.psg-clusters.psg-compact .psg-cluster-grid .psg-photo,.psg-clusters.psg-compact .psg-cluster-grid .psg-photo.psg-portrait{grid-column:span 3}
.psg-clusters.psg-compact .psg-cluster-grid .psg-photo.psg-feature,.psg-clusters.psg-compact .psg-cluster-grid .psg-photo.psg-wide{grid-column:span 6}
.psg-clusters.psg-compact .psg-cluster-grid .psg-photo.psg-feature.psg-portrait{grid-column:span 3}

/* The all-photo viewer uses explicit geometry. It does not inherit the old CSS grid. */
.viewer-all-grid.psg-gallery,.psg-all{display:block!important;position:relative!important;grid-template-columns:none!important;grid-auto-rows:auto!important;align-content:initial!important;min-height:0;overflow:auto;padding:clamp(6px,1.4vw,16px)!important;background:#101113;overscroll-behavior:contain;scrollbar-gutter:stable}
.psg-justified-canvas{position:relative;width:100%;min-height:1px}
.viewer-all-grid.psg-gallery button.psg-photo,.psg-all button.psg-photo{position:absolute!important;display:block!important;aspect-ratio:auto!important;border-radius:2px!important;background:#222327!important;transform:none!important}
.viewer-all-grid.psg-gallery button.psg-photo img,.psg-all button.psg-photo img{width:100%!important;height:100%!important;object-fit:cover!important}

@media(max-width:760px){
 .psg-quiet{padding:16px 12px 24px}.psg-quiet-head{margin-bottom:20px}.psg-quiet-head h2{font-size:1.35rem}
 .psg-quiet-main{height:min(52dvh,520px)}.psg-quiet-strip{gap:5px}.psg-quiet-strip .psg-photo{height:54px}
 .psg-clusters{padding:22px 12px 36px}.psg-clusters-head{margin-bottom:42px}.psg-clusters-head h2{font-size:1.8rem}
 .psg-cluster{margin-bottom:56px}.psg-cluster-header{align-items:flex-start;gap:10px}
 .psg-cluster-copy{display:grid;grid-template-columns:1fr;gap:3px}.psg-cluster-copy p{font-size:.74rem}
 .psg-cluster-grid{grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}
 .psg-cluster-grid .psg-photo,.psg-cluster-grid .psg-photo.psg-portrait{grid-column:span 3}
 .psg-cluster-grid .psg-photo.psg-feature,.psg-cluster-grid .psg-photo.psg-wide{grid-column:span 6}
 .psg-cluster-grid .psg-photo.psg-feature.psg-portrait{grid-column:span 3}
 .viewer-all-grid.psg-gallery,.psg-all{padding:6px!important}
}
@media(max-height:560px){.psg-quiet-main{height:58dvh}.psg-quiet-strip .psg-photo{height:48px}}
@media(prefers-reduced-motion:reduce){.psg-gallery *{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
"""


GALLERY_JS = r"""
(()=>{
'use strict';
const instances=new WeakMap(),ratioCache=new Map();let instanceSequence=0;
const allowedModes=new Set(['quiet_memories','moment_clusters','all']);
const clamp=(value,minimum,maximum)=>Math.max(minimum,Math.min(maximum,value));
const text=(value,fallback='')=>String(value||fallback).trim();
function rememberRatio(key,ratio){
 if(!key||!Number.isFinite(ratio)||ratio<=0)return;
 ratioCache.set(key,clamp(ratio,.12,8));
 if(ratioCache.size>4096){let count=0;for(const oldKey of ratioCache.keys()){ratioCache.delete(oldKey);if(++count>=512)break}}
}
function sourceFor(tile){
 const image=tile.querySelector('img'),thumb=text(tile.dataset.thumb)||(image?text(image.currentSrc||image.getAttribute('src')):'');
 return {thumb,preview:text(tile.dataset.preview)||thumb};
}
function ratioFor(tile,source){
 const width=Number(tile.dataset.width),height=Number(tile.dataset.height);
 if(width>0&&height>0){const ratio=width/height;rememberRatio(source.thumb,ratio);return clamp(ratio,.12,8)}
 const image=tile.querySelector('img');
 if(image&&image.naturalWidth>0&&image.naturalHeight>0){const ratio=image.naturalWidth/image.naturalHeight;rememberRatio(source.thumb,ratio);return clamp(ratio,.12,8)}
 return ratioCache.get(source.thumb)||4/3;
}
function justifiedGeometry(inputRatios,width,gap,target){
 const ratios=Array.from(inputRatios||[],value=>clamp(Number(value)||4/3,.12,8)),boxes=[];
 if(!ratios.length||!Number.isFinite(width)||width<=0)return {boxes,height:0};
 const safeGap=Math.max(0,Number(gap)||0),safeTarget=Math.max(1,Number(target)||1);let y=0,start=0;
 while(start<ratios.length){let end=start,sum=0;while(end<ratios.length){sum+=ratios[end];end++;if(sum*safeTarget+safeGap*(end-start-1)>=width)break}
  const last=end===ratios.length,available=Math.max(1,width-safeGap*(end-start-1)),natural=available/sum,rowHeight=last?Math.min(safeTarget,natural):natural;let x=0;
  for(let index=start;index<end;index++){const boxWidth=Math.max(1,ratios[index]*rowHeight);boxes.push({left:x,top:y,width:boxWidth,height:Math.max(1,rowHeight)});x+=boxWidth+safeGap}
  y+=rowHeight+safeGap;start=end;
 }
 return {boxes,height:Math.max(0,y-safeGap)};
}
function readTile(tile,index){
 const source=sourceFor(tile),chapter=tile.closest('.chapter');
 return {tile,index,source,ratio:ratioFor(tile,source),alt:text(tile.dataset.alt,'사진'),title:text(tile.dataset.title,'사진'),date:text(tile.dataset.date),location:text(tile.dataset.location),people:text(tile.dataset.people),chapter,chapterTitle:text(chapter?.querySelector('h2')?.textContent,'사진 이야기'),chapterCopy:text(chapter?.querySelector('.chapter-copy')?.textContent)};
}
function element(tag,className='',content=''){
 const node=document.createElement(tag);if(className)node.className=className;if(content)node.textContent=content;return node;
}
function imageFor(item,kind='thumb'){
 const image=element('img'),source=kind==='preview'?item.source.preview:item.source.thumb;image.alt=item.alt;image.decoding='async';image.loading=kind==='preview'?'eager':'lazy';if(source)image.src=source;image.draggable=false;return image;
}
function buttonFor(item,action='open',kind='thumb'){
 const button=element('button','psg-photo');button.type='button';button.dataset.psgIndex=String(item.index);button.dataset.psgAction=action;
 button.setAttribute('aria-label',(item.index+1)+'번째 사진, '+item.alt);button.style.aspectRatio=String(item.ratio);button.append(imageFor(item,kind));return button;
}
function nearestScrollable(node){
 let current=node;
 while(current&&current!==document.body){const style=getComputedStyle(current),overflow=style.overflowY;if((overflow==='auto'||overflow==='scroll')&&current.scrollHeight>current.clientHeight)return current;current=current.parentElement}
 return document.scrollingElement||document.documentElement;
}
function scrollAnchor(host){
 const scroller=nearestScrollable(host),buttons=[...host.querySelectorAll('[data-psg-index]')];
 if(scroller===document.scrollingElement||scroller===document.documentElement){const button=buttons.find(item=>item.getBoundingClientRect().bottom>0);return button?{scroller,index:button.dataset.psgIndex,offset:button.getBoundingClientRect().top}:null}
 const top=scroller.getBoundingClientRect().top,button=buttons.find(item=>item.getBoundingClientRect().bottom>top);return button?{scroller,index:button.dataset.psgIndex,offset:button.getBoundingClientRect().top-top}:null;
}
function restoreAnchor(host,anchor){
 if(!anchor)return;const button=host.querySelector('[data-psg-index="'+anchor.index+'"]');if(!button)return;
 if(anchor.scroller===document.scrollingElement||anchor.scroller===document.documentElement){window.scrollBy(0,button.getBoundingClientRect().top-anchor.offset);return}
 anchor.scroller.scrollTop+=button.getBoundingClientRect().top-anchor.scroller.getBoundingClientRect().top-anchor.offset;
}
function directionalFocus(host,current,key){
 const candidates=[...host.querySelectorAll('button[data-psg-index]')].filter(button=>!button.disabled&&button.offsetParent!==null);if(!candidates.length)return;
 if(key==='Home'||key==='End'){candidates[key==='Home'?0:candidates.length-1].focus({preventScroll:true});return}
 const origin=current.getBoundingClientRect(),cx=origin.left+origin.width/2,cy=origin.top+origin.height/2;let best=null,bestScore=Infinity;
 for(const candidate of candidates){if(candidate===current)continue;const rect=candidate.getBoundingClientRect(),x=rect.left+rect.width/2,y=rect.top+rect.height/2,dx=x-cx,dy=y-cy;
  const valid=key==='ArrowRight'?dx>2:key==='ArrowLeft'?dx<-2:key==='ArrowDown'?dy>2:dy<-2;if(!valid)continue;
  const primary=Math.abs(key==='ArrowRight'||key==='ArrowLeft'?dx:dy),cross=Math.abs(key==='ArrowRight'||key==='ArrowLeft'?dy:dx),score=primary+cross*2.4;
  if(score<bestScore){best=candidate;bestScore=score}
 }
 if(best){best.focus({preventScroll:true});best.scrollIntoView({block:'nearest',inline:'nearest'})}
}
function mount(options={}){
 const host=options.host;if(!(host instanceof Element))throw new TypeError('PhotosStoryGallery host must be an Element');
 instances.get(host)?.destroy();
 const tileList=Array.from(options.tiles||[]).filter(tile=>tile instanceof Element&&!tile.hidden),items=tileList.map(readTile);
 const mode=allowedModes.has(options.mode)?options.mode:'all',reduceMotion=Boolean(options.reduceMotion),compact=Boolean(options.compact),onOpen=typeof options.onOpen==='function'?options.onOpen:()=>{};
 const originalClass=host.getAttribute('class'),originalStyle=host.getAttribute('style'),originalLabel=host.getAttribute('aria-label');
 const instanceId=++instanceSequence;let destroyed=false,frame=0,activeIndex=clamp(Number.isInteger(options.initialTile)?options.initialTile:Math.max(0,tileList.indexOf(options.initialTile)),0,Math.max(0,items.length-1)),resizeObserver=null,lastWidth=-1,canvas=null;
 const cleanups=[];host.replaceChildren();host.classList.add('psg-gallery','psg-'+(mode==='quiet_memories'?'quiet':mode==='moment_clusters'?'clusters':'all'));if(reduceMotion)host.classList.add('psg-reduce-motion');if(compact)host.classList.add('psg-compact');
 host.setAttribute('aria-label',mode==='all'?'전체 사진':mode==='quiet_memories'?'조용한 추억 사진 보기':'장면별 사진 이야기');
 function listen(target,name,handler,settings){target.addEventListener(name,handler,settings);cleanups.push(()=>target.removeEventListener(name,handler,settings))}
 function scheduleLayout(){if(destroyed||mode!=='all')return;cancelAnimationFrame(frame);frame=requestAnimationFrame(layoutAll)}
 function clearImageError(image){const button=image.closest?.('button[data-psg-index]');if(!button||!host.contains(button))return;button.classList.remove('psg-load-error');button.querySelector('.psg-photo-error')?.remove();button.removeAttribute('aria-describedby')}
 function markImageError(image){const button=image.closest?.('button[data-psg-index]');if(!button||!host.contains(button)||button.classList.contains('psg-load-error'))return;const index=Number(button.dataset.psgIndex),notice=element('span','psg-photo-error','사진을 불러오지 못했어요');notice.id='psg-photo-error-'+instanceId+'-'+index+'-'+button.dataset.psgAction;button.classList.add('psg-load-error');button.setAttribute('aria-describedby',notice.id);button.append(notice)}
 function updateKnownRatio(image){
  const button=image.closest('[data-psg-index]'),index=Number(button?.dataset.psgIndex),item=items[index];if(!item||!image.naturalWidth||!image.naturalHeight)return;
  const ratio=clamp(image.naturalWidth/image.naturalHeight,.12,8);rememberRatio(item.source.thumb,ratio);if(Math.abs(item.ratio-ratio)<.01)return;item.ratio=ratio;button.style.aspectRatio=String(ratio);
  if(mode==='all')scheduleLayout();if(mode==='quiet_memories')updateQuietStripGeometry();if(mode==='moment_clusters')classifyCluster(button,item,button.classList.contains('psg-feature'));
 }
 function openItem(index){const item=items[index];if(!item)return;activeIndex=index;onOpen(item.tile)}
 function updateQuietStripGeometry(){const height=compact?54:74;host.querySelectorAll('.psg-quiet-strip [data-psg-index]').forEach(button=>{const item=items[Number(button.dataset.psgIndex)];if(item)button.style.width=clamp(height*item.ratio,compact?42:48,compact?118:160)+'px'})}
 function selectQuiet(index,focus=false,animate=false){
  const item=items[index];if(!item)return;activeIndex=index;const main=host.querySelector('.psg-quiet-main'),image=main?.querySelector('img');if(main&&image){main.dataset.psgIndex=String(index);main.setAttribute('aria-label','크게 보기, '+item.alt);image.alt=item.alt;if(item.source.preview)image.src=item.source.preview;else image.removeAttribute('src')}
  const caption=host.querySelector('.psg-quiet-caption'),strong=caption?.querySelector('strong'),detail=caption?.querySelector('span');if(strong)strong.textContent=item.chapterTitle||item.title;if(detail){const date=item.date&&!item.chapterCopy.includes(item.date)?item.date:'';detail.textContent=[item.chapterCopy,date,item.location,item.people].filter(Boolean).join(' · ')}
  const position=host.querySelector('.psg-quiet-position');if(position)position.textContent=(index+1)+' / '+items.length;
  host.querySelectorAll('.psg-quiet-strip [data-psg-index]').forEach(button=>{const selected=Number(button.dataset.psgIndex)===index;button.setAttribute('aria-current',String(selected));button.setAttribute('aria-pressed',String(selected))});
  const selected=host.querySelector('.psg-quiet-strip [data-psg-index="'+index+'"]'),strip=selected?.closest('.psg-quiet-strip');if(selected&&strip){const selectedRect=selected.getBoundingClientRect(),stripRect=strip.getBoundingClientRect(),left=strip.scrollLeft+selectedRect.left-stripRect.left-(strip.clientWidth-selectedRect.width)/2;strip.scrollTo({left:Math.max(0,left),behavior:animate&&!reduceMotion?'smooth':'auto'})}if(focus)selected?.focus({preventScroll:true});
 }
 function renderQuiet(){
  if(!items.length){host.append(element('p','psg-empty','표시할 사진이 없습니다.'));return}
  if(options.title||options.intro){const head=element('header','psg-quiet-head');if(options.title)head.append(element('h2','',text(options.title)));if(options.intro)head.append(element('p','',text(options.intro)));host.append(head)}
  const stage=element('figure','psg-quiet-stage'),main=buttonFor(items[activeIndex],'open','preview');main.classList.add('psg-quiet-main');
  const caption=element('figcaption','psg-quiet-caption');caption.append(element('strong'));caption.append(element('span'));stage.append(main,caption);
  const strip=element('div','psg-quiet-strip');strip.setAttribute('role','group');strip.setAttribute('aria-label','사진 선택');
  items.forEach(item=>{const button=buttonFor(item,'select');button.setAttribute('aria-pressed','false');strip.append(button)});
  const position=element('div','psg-quiet-position');host.append(stage,strip,position);updateQuietStripGeometry();selectQuiet(activeIndex);
 }
 function groups(){
  const result=[],byChapter=new Map();for(const item of items){const key=item.chapter||('date:'+item.date);let group=byChapter.get(key);if(!group){group={title:item.chapterTitle||item.date||'사진 이야기',copy:item.chapterCopy,items:[]};byChapter.set(key,group);result.push(group)}group.items.push(item)}return result;
 }
 function classifyCluster(button,item,feature){button.classList.remove('psg-feature','psg-wide','psg-portrait');if(feature)button.classList.add('psg-feature');if(item.ratio>=1.55)button.classList.add('psg-wide');else if(item.ratio<=.82)button.classList.add('psg-portrait')}
 function renderClusters(){
  if(!items.length){host.append(element('p','psg-empty','표시할 사진이 없습니다.'));return}
  if(options.title||options.intro){const head=element('header','psg-clusters-head');if(options.title)head.append(element('h2','',text(options.title)));if(options.intro)head.append(element('p','',text(options.intro)));host.append(head)}
  groups().forEach((group,groupIndex)=>{const section=element('section','psg-cluster'),header=element('header','psg-cluster-header'),order=element('span','psg-cluster-order',String(groupIndex+1).padStart(2,'0')),copy=element('div','psg-cluster-copy');copy.append(element('h3','',group.title));if(group.copy)copy.append(element('p','',group.copy));header.append(order,copy);
   const grid=element('div','psg-cluster-grid');grid.setAttribute('role','group');grid.setAttribute('aria-label',group.title);group.items.forEach((item,index)=>{const button=buttonFor(item);classifyCluster(button,item,index===0&&group.items.length>2);grid.append(button)});section.append(header,grid);host.append(section)});
 }
 function layoutAll(){
  frame=0;if(destroyed||!canvas)return;const width=Math.floor(canvas.clientWidth);if(width<=0)return;const anchor=scrollAnchor(host),gap=compact?5:9,target=compact?168:172,models=items.map(item=>({item,button:canvas.querySelector('[data-psg-index="'+item.index+'"]')})),geometry=justifiedGeometry(models.map(model=>model.item.ratio),width,gap,target);
  geometry.boxes.forEach((box,index)=>{const button=models[index]?.button;if(!button)return;button.style.left=box.left+'px';button.style.top=box.top+'px';button.style.width=box.width+'px';button.style.height=box.height+'px'});
  canvas.style.height=geometry.height+'px';restoreAnchor(host,anchor);
 }
 function renderAll(){
  if(!items.length){host.append(element('p','psg-empty','표시할 사진이 없습니다.'));return}
  canvas=element('div','psg-justified-canvas');items.forEach(item=>canvas.append(buttonFor(item)));host.append(canvas);scheduleLayout();
 }
 listen(host,'load',event=>{if(event.target instanceof HTMLImageElement){clearImageError(event.target);updateKnownRatio(event.target)}},true);
 listen(host,'error',event=>{if(event.target instanceof HTMLImageElement)markImageError(event.target)},true);
 if(mode==='quiet_memories')renderQuiet();else if(mode==='moment_clusters')renderClusters();else renderAll();
 listen(host,'click',event=>{const button=event.target.closest?.('button[data-psg-index]');if(!button||!host.contains(button))return;const index=Number(button.dataset.psgIndex);activeIndex=index;if(button.dataset.psgAction==='select'){selectQuiet(index,false,true);return}openItem(index)});
 listen(host,'keydown',event=>{if(mode!=='all'&&document.querySelector('[data-viewer].open,[data-viewer][open]'))return;const button=event.target.closest?.('button[data-psg-index]');if(!button)return;if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key)){event.preventDefault();directionalFocus(host,button,event.key)}});
 listen(host,'focusin',event=>{const button=event.target.closest?.('button[data-psg-index]');if(button)activeIndex=Number(button.dataset.psgIndex)});
 if('ResizeObserver' in window){resizeObserver=new ResizeObserver(entries=>{const width=Math.round(entries[0]?.contentRect.width||0);if(width===lastWidth)return;lastWidth=width;scheduleLayout()});resizeObserver.observe(host)}else{const resize=()=>scheduleLayout();listen(window,'resize',resize)}
 const api={destroy(){if(destroyed)return;destroyed=true;cancelAnimationFrame(frame);resizeObserver?.disconnect();cleanups.splice(0).forEach(cleanup=>cleanup());host.replaceChildren();if(originalClass===null)host.removeAttribute('class');else host.setAttribute('class',originalClass);if(originalStyle===null)host.removeAttribute('style');else host.setAttribute('style',originalStyle);if(originalLabel===null)host.removeAttribute('aria-label');else host.setAttribute('aria-label',originalLabel);instances.delete(host)},getActiveTile(){return items[activeIndex]?.tile||null}};
 instances.set(host,api);return api;
}
window.PhotosStoryGallery=Object.freeze({mount});
})();
"""


__all__ = ["GALLERY_CSS", "GALLERY_JS"]
