"""Spatial ribbon Story presentation.

The module deliberately owns only the spatial chapter stage. The source photo
tiles remain the canonical Story data and the shared lightbox remains the only
full-screen viewer. This keeps theme switching reversible and avoids copying
private photo metadata into another application state.
"""

SPATIAL_CSS = r"""
.photos-spatial{
  --sp-ink:#f0f0f2;--sp-muted:#aeb0b7;--sp-line:rgba(255,255,255,.18);
  --sp-surface:#17191d;--sp-stage:#101216;--sp-focus:#f1f1f3;
  position:relative;isolation:isolate;overflow:hidden;color:var(--sp-ink);
  background:
    radial-gradient(ellipse at 50% 18%,rgba(104,111,126,.22),transparent 43%),
    linear-gradient(180deg,#1b1e23 0%,#111318 69%,#17191d 100%);
  border:1px solid rgba(255,255,255,.1);border-radius:10px;
  box-shadow:0 26px 70px rgba(4,6,10,.26);
}
.photos-spatial__header{position:relative;z-index:3;display:grid;justify-items:center;gap:7px;padding:clamp(22px,3vw,38px) clamp(20px,4vw,56px) 10px;text-align:center}
.photos-spatial__title{margin:0;max-width:22ch;color:var(--sp-ink);font:500 clamp(1.35rem,2.7vw,2.25rem)/1.35 ui-serif,"AppleMyungjo","Batang",Georgia,serif;letter-spacing:-.035em;word-break:keep-all}
.photos-spatial__intro{margin:0;max-width:62ch;color:var(--sp-muted);font-size:.84rem;line-height:1.75;word-break:keep-all}
.photos-spatial__stage{position:relative;z-index:1;min-height:clamp(430px,55dvh,610px);padding:34px 0 104px;perspective:1900px;perspective-origin:50% 44%;overflow:hidden;touch-action:pan-y;outline:none}
.photos-spatial__stage::before{content:"";position:absolute;z-index:-2;left:50%;bottom:22px;width:min(96%,1240px);height:48%;transform:translateX(-50%) perspective(760px) rotateX(66deg);transform-origin:center bottom;background:radial-gradient(ellipse at 50% 9%,rgba(255,255,255,.17),rgba(95,101,113,.075) 32%,rgba(16,18,22,.035) 66%,transparent 76%);filter:blur(2px);pointer-events:none}
.photos-spatial__stage::after{content:"";position:absolute;z-index:-1;left:5%;right:5%;bottom:52px;height:1px;background:linear-gradient(90deg,transparent,rgba(255,255,255,.2),transparent);box-shadow:0 25px 48px 17px rgba(178,184,198,.07);pointer-events:none}
.photos-spatial__track{align-items:center;transform-style:preserve-3d}
.photos-spatial__slide{position:relative;height:clamp(285px,32dvh,340px);display:flex;align-items:center;justify-content:center;padding:0 4px;overflow:visible;background:transparent;border:0;color:inherit;cursor:grab;transform-style:preserve-3d;touch-action:pan-y;transition:visibility 0s linear,opacity .22s ease}
.photos-spatial__slide:active{cursor:grabbing}
.photos-spatial__slide:focus-visible{outline:0}
.photos-spatial__slide:focus-visible .photos-spatial__frame{box-shadow:0 0 0 3px var(--sp-focus),0 24px 48px rgba(0,0,0,.5)}
.photos-spatial__inner{position:relative;flex:0 0 auto;width:240px;height:300px;display:flex;align-items:center;justify-content:center;transform-style:preserve-3d;transform-origin:50% 58%;transition:width .22s ease,height .22s ease}
.photos-spatial__frame{position:relative;width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#0d0f12;border:1px solid rgba(255,255,255,.27);border-radius:2px;box-shadow:0 26px 48px rgba(0,0,0,.47),inset 0 1px 0 rgba(255,255,255,.18);-webkit-box-reflect:below 8px linear-gradient(to bottom,rgba(255,255,255,0) 61%,rgba(255,255,255,.11) 100%)}
.photos-spatial__image{display:block;width:100%;height:100%;object-fit:contain;background:#0d0f12;user-select:none;-webkit-user-drag:none;opacity:0;transition:opacity .24s ease}
.photos-spatial__image.is-ready{opacity:1}
.photos-spatial__loading{position:absolute;inset:0;display:grid;place-items:center;color:#8f929b;font-size:.76rem;background:linear-gradient(115deg,#111318 20%,#20232a 42%,#111318 64%);background-size:220% 100%;animation:photos-spatial-loading 1.4s ease-in-out infinite}
.photos-spatial__image.is-ready+.photos-spatial__loading{display:none}
.photos-spatial__image.is-failed+.photos-spatial__loading{background:#15171b;animation:none}
.photos-spatial__chapter{width:min(88%,680px);margin:0;text-align:center;color:#d9d9dd;font-size:.83rem;line-height:1.65;pointer-events:none}
.photos-spatial__chapter strong,.photos-spatial__chapter span{display:block}.photos-spatial__chapter strong{font-weight:650}.photos-spatial__chapter span{margin-top:2px;color:#9fa1a9;font-size:.74rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.photos-spatial__nav{position:absolute;z-index:5;top:50%;width:46px;height:46px;min-width:46px;min-height:46px;padding:0;border:1px solid rgba(255,255,255,.28);border-radius:50%;background:rgba(20,22,27,.64);color:#f4f4f6;font:500 1.3rem/1 system-ui;backdrop-filter:blur(10px);transform:translateY(-50%);cursor:pointer}
.photos-spatial__nav:hover{background:rgba(42,45,52,.86)}.photos-spatial__nav:disabled{opacity:.24;cursor:default}.photos-spatial__nav--prev{left:max(12px,2vw)}.photos-spatial__nav--next{right:max(12px,2vw)}
.photos-spatial__footer{position:relative;z-index:4;display:grid;justify-items:center;gap:10px;margin-top:-62px;padding:0 clamp(18px,4vw,52px) 26px}
.photos-spatial__counter{color:#c6c7cc;font-size:.78rem;font-variant-numeric:tabular-nums}
.photos-spatial__timeline{display:flex;align-items:center;justify-content:center;max-width:min(100%,760px);overflow-x:auto;scrollbar-width:none}.photos-spatial__timeline::-webkit-scrollbar{display:none}
.photos-spatial__timeline[hidden]{display:none}
.photos-spatial__timeline-button{position:relative;flex:0 0 auto;min-width:76px;min-height:40px;padding:8px 18px;border:0;border-bottom:1px solid rgba(255,255,255,.22);border-radius:0;background:transparent;color:#8f929a;font:500 .75rem/1.2 system-ui;white-space:nowrap;cursor:pointer}
.photos-spatial__timeline-button[aria-current=true]{color:#f2f2f4;border-bottom-color:#f2f2f4}.photos-spatial__timeline-button:focus-visible{outline:2px solid #f2f2f4;outline-offset:-2px}
.photos-spatial__hint{margin:0;color:#898c94;font-size:.69rem;text-align:center}
.photos-spatial--empty{min-height:320px;display:grid;place-items:center;padding:32px;color:var(--sp-muted)}
@keyframes photos-spatial-loading{0%{background-position:100% 0}100%{background-position:-100% 0}}
@media(max-width:760px){
 .photos-spatial{border-radius:0;border-inline:0}
 .photos-spatial__header{padding:20px 16px 4px}.photos-spatial__title{font-size:1.22rem;line-height:1.5}.photos-spatial__intro{font-size:.75rem;line-height:1.65;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
 .photos-spatial__stage{min-height:410px;height:55dvh;max-height:520px;padding:24px 0 104px;perspective:1150px}
 .photos-spatial__slide{height:clamp(245px,33dvh,300px);padding-inline:2px}
 .photos-spatial__stage::before{bottom:65px;width:130%;height:42%}
 .photos-spatial__stage::after{left:3%;right:3%;bottom:91px}
 .photos-spatial__nav{display:none}.photos-spatial__chapter{width:88%;font-size:.77rem}.photos-spatial__chapter span{font-size:.68rem}
 .photos-spatial__footer{margin-top:-58px;padding:0 12px 20px;gap:9px}.photos-spatial__timeline{justify-content:flex-start;width:100%}.photos-spatial__timeline-button{min-width:70px;padding-inline:13px}
}
@media(prefers-reduced-motion:reduce){
 .photos-spatial__slide,.photos-spatial__inner,.photos-spatial__image{transition:none!important}
 .photos-spatial__loading{animation:none}
}
"""


SPATIAL_JS = r"""
(()=>{
'use strict';
const d=document;
const clamp=(value,minimum,maximum)=>Math.max(minimum,Math.min(maximum,value));
const text=(parent,className,value)=>{const node=d.createElement('span');node.className=className;node.textContent=value||'';parent.append(node);return node};
function inert(host){host.replaceChildren();host.className='photos-spatial photos-spatial--empty';host.textContent='표시할 사진이 없습니다.';return{destroy(){host.replaceChildren();host.classList.remove('photos-spatial','photos-spatial--empty')},getActiveTile(){return null}}}
function mount(options={}){
 const host=options.host,sourceTiles=Array.from(options.tiles||[]).filter(tile=>tile&&tile.dataset);
 if(!host||typeof host.replaceChildren!=='function')throw new TypeError('PhotosStorySpatial.mount requires a host element');
 if(host.__photosStorySpatial?.destroy)host.__photosStorySpatial.destroy();
 if(!sourceTiles.length)return inert(host);
 const reduceMotion=Boolean(options.reduceMotion),compact=Boolean(options.compact),onOpen=typeof options.onOpen==='function'?options.onOpen:()=>{};
 const abort=new AbortController(),signal=abort.signal;
 let destroyed=false,swiper=null,activeIndex=Math.max(0,sourceTiles.indexOf(options.initialTile)),lastSyncedIndex=-1;
 const records=sourceTiles.map((tile,index)=>{
  const chapter=tile.closest?.('.chapter'),heading=chapter?.querySelector('.chapter-head h2, h2'),copy=chapter?.querySelector('.chapter-copy');
  const sourceImage=tile.querySelector?.('img'),naturalWidth=sourceImage?.naturalWidth||0,naturalHeight=sourceImage?.naturalHeight||0;
  return{tile,index,title:tile.dataset.title||tile.dataset.alt||'사진',alt:tile.dataset.alt||'',date:tile.dataset.date||'',location:tile.dataset.location||'',people:tile.dataset.people||'',preview:tile.dataset.preview||'',thumb:tile.dataset.thumb||sourceImage?.currentSrc||sourceImage?.getAttribute('src')||tile.dataset.preview||'',chapter,chapterTitle:heading?.textContent?.trim()||'사진 모음',chapterIntro:copy?.textContent?.trim()||'',ratio:naturalWidth&&naturalHeight?naturalWidth/naturalHeight:.82};
 });
 const groups=[];records.forEach(record=>{let group=groups[groups.length-1];if(!group||group.chapter!==record.chapter){group={chapter:record.chapter,title:record.chapterTitle,intro:record.chapterIntro,start:record.index,end:record.index};groups.push(group)}else group.end=record.index});
 host.className='photos-spatial'+(compact?' photos-spatial--compact':'');host.replaceChildren();
 const header=d.createElement('header');header.className='photos-spatial__header';
 const title=d.createElement('h2');title.className='photos-spatial__title';title.textContent=String(options.title||records[0].chapterTitle||'사진 이야기');header.append(title);
 if(options.intro){const intro=d.createElement('p');intro.className='photos-spatial__intro';intro.textContent=String(options.intro);header.append(intro)}
 const stage=d.createElement('div');stage.className='photos-spatial__stage swiper';stage.tabIndex=0;stage.setAttribute('role','region');stage.setAttribute('aria-roledescription','사진 회전 갤러리');stage.setAttribute('aria-label',title.textContent);
 const track=d.createElement('div');track.className='photos-spatial__track swiper-wrapper';stage.append(track);
 const slides=records.map(record=>{
  const slide=d.createElement('button');slide.type='button';slide.tabIndex=-1;slide.className='photos-spatial__slide swiper-slide';slide.dataset.index=String(record.index);slide.setAttribute('aria-current','false');slide.setAttribute('aria-label',(record.index+1)+' / '+records.length+', '+record.title);
  const inner=d.createElement('span');inner.className='photos-spatial__inner';
  const frame=d.createElement('span');frame.className='photos-spatial__frame';
  const image=d.createElement('img');image.className='photos-spatial__image';image.alt=record.alt;image.decoding='async';image.draggable=false;
  const loading=d.createElement('span');loading.className='photos-spatial__loading';loading.setAttribute('aria-hidden','true');loading.textContent='사진 불러오는 중';
  frame.append(image,loading);inner.append(frame);slide.append(inner);track.append(slide);record.slide=slide;record.inner=inner;record.image=image;record.loading=loading;sizeFrame(record,record.ratio);return slide;
 });
 const prev=d.createElement('button');prev.type='button';prev.className='photos-spatial__nav photos-spatial__nav--prev';prev.setAttribute('aria-label','이전 사진');prev.textContent='←';
 const next=d.createElement('button');next.type='button';next.className='photos-spatial__nav photos-spatial__nav--next';next.setAttribute('aria-label','다음 사진');next.textContent='→';
 const chapter=d.createElement('p');chapter.className='photos-spatial__chapter';chapter.setAttribute('aria-live','polite');const chapterStrong=d.createElement('strong'),chapterIntro=d.createElement('span');chapter.append(chapterStrong,chapterIntro);
 stage.append(prev,next);
 const footer=d.createElement('footer');footer.className='photos-spatial__footer';
 const counter=text(footer,'photos-spatial__counter','');counter.setAttribute('aria-live','polite');
 const timeline=d.createElement('nav');timeline.className='photos-spatial__timeline';timeline.setAttribute('aria-label','Story 장면');
 const timelineButtons=groups.map(group=>{const button=d.createElement('button');button.type='button';button.className='photos-spatial__timeline-button';button.textContent=group.title;button.dataset.start=String(group.start);button.addEventListener('click',()=>select(group.start,true),{signal});timeline.append(button);return button});
 timeline.hidden=groups.length===1;footer.append(chapter,timeline,counter);const hint=d.createElement('p');hint.className='photos-spatial__hint';hint.textContent=compact?'좌우로 넘기고 사진을 선택하세요':'드래그하거나 방향키로 사진을 넘기세요';footer.append(hint);host.append(header,stage,footer);
 function groupFor(index){return groups.find(group=>index>=group.start&&index<=group.end)||groups[0]}
 function sizeFrame(record,rawRatio){
  const ratio=clamp(Number(rawRatio)||.82,.62,1.58),maxHeight=compact?232:282,maxWidth=compact?252:372,height=Math.min(maxHeight,maxWidth/ratio),width=height*ratio;
  record.ratio=ratio;record.inner.style.width=Math.round(width)+'px';record.inner.style.height=Math.round(height)+'px';
 }
 function load(record,quality){
  if(!record?.image)return;const source=quality==='preview'?record.preview:(record.thumb||record.preview);if(!source)return;
  if(record.image.dataset.quality==='preview'||record.image.dataset.source===source)return;
  record.image.addEventListener('load',()=>{sizeFrame(record,record.image.naturalWidth/record.image.naturalHeight);record.image.classList.remove('is-failed');record.image.classList.add('is-ready');paint(swiper)},{once:true,signal});
  record.image.addEventListener('error',()=>{record.image.classList.remove('is-ready');record.image.classList.add('is-failed');record.loading.textContent='사진을 불러오지 못했어요'},{once:true,signal});
  record.image.dataset.quality=quality;record.image.dataset.source=source;record.image.src=source;
 }
 function loadWindow(index){for(let item=Math.max(0,index-4);item<=Math.min(records.length-1,index+4);item++){const distance=Math.abs(item-index);load(records[item],distance<=1?'preview':'thumb')}}
 function sync(index){
  activeIndex=clamp(Number(index)||0,0,records.length-1);const group=groupFor(activeIndex);counter.textContent=(activeIndex+1)+' / '+records.length;chapterStrong.textContent=group.title;chapterIntro.textContent=group.intro;
  timelineButtons.forEach((button,item)=>button.setAttribute('aria-current',String(groups[item]===group)));prev.disabled=activeIndex===0;next.disabled=activeIndex===records.length-1;
  if(lastSyncedIndex!==activeIndex){const previous=slides[lastSyncedIndex];if(previous){previous.tabIndex=-1;previous.setAttribute('aria-current','false')}const current=slides[activeIndex];current.tabIndex=0;current.setAttribute('aria-current','true');lastSyncedIndex=activeIndex}loadWindow(activeIndex);
 }
 function depthFor(progress){const distance=Math.min(3.7,Math.abs(progress)),side=progress<0?-1:1,scale=(compact?.6:.61)+(compact?.78:.77)/(1+distance*(compact?1.65:1.25)),outward=distance<.08?0:-side*Math.max(18,82-distance*14);return{distance,x:outward,rotate:distance<.08?0:-side*Math.min(34,8+distance*9),y:Math.pow(distance,1.45)*(compact?9:16),z:-Math.pow(distance,1.16)*(compact?68:108),scale,opacity:Math.max(.22,1-distance*.19),brightness:Math.max(.5,1-distance*.115)}}
 let painted=new Set();
 function paint(instance){
  const center=instance?.activeIndex??activeIndex,nextPainted=new Set();for(let index=Math.max(0,center-5);index<=Math.min(records.length-1,center+5);index++)nextPainted.add(index);
  new Set([...painted,...nextPainted]).forEach(index=>{const record=records[index],progress=Number.isFinite(record.slide.progress)?record.slide.progress:index-center,depth=depthFor(progress),visible=nextPainted.has(index)&&depth.distance<=(compact?1.65:3.65);record.slide.style.visibility=visible?'visible':'hidden';record.slide.style.opacity=visible?String(depth.opacity):'0';record.slide.style.zIndex=String(100-Math.round(depth.distance*10));record.inner.style.willChange=visible?'transform,opacity,filter':'auto';if(visible){record.inner.style.transform='translate3d('+depth.x+'px,'+depth.y+'px,'+depth.z+'px) rotateY('+depth.rotate+'deg) scale('+depth.scale+')';record.inner.style.filter='brightness('+depth.brightness+') saturate('+(1-depth.distance*.055)+')'}});painted=nextPainted;
 }
 function select(index,focus=false){const nextIndex=clamp(index,0,records.length-1);if(swiper)swiper.slideTo(nextIndex,reduceMotion?0:520);else{sync(nextIndex);paint(null);records[nextIndex].slide.scrollIntoView({behavior:reduceMotion?'auto':'smooth',inline:'center',block:'nearest'})}if(focus)records[nextIndex].slide.focus({preventScroll:true})}
 slides.forEach((slide,index)=>slide.addEventListener('click',()=>{if(index===activeIndex)onOpen(records[index].tile);else select(index,true)},{signal}));
 prev.addEventListener('click',()=>select(activeIndex-1,true),{signal});next.addEventListener('click',()=>select(activeIndex+1,true),{signal});
 stage.addEventListener('keydown',event=>{if(d.querySelector('[data-viewer]')?.open)return;let target=null;if(event.key==='ArrowLeft')target=activeIndex-1;if(event.key==='ArrowRight')target=activeIndex+1;if(event.key==='Home')target=0;if(event.key==='End')target=records.length-1;if(target!==null){event.preventDefault();select(target,true)}else if((event.key==='Enter'||event.key===' ')&&event.target===stage){event.preventDefault();onOpen(records[activeIndex].tile)}},{signal});
 if(typeof window.Swiper==='function'){
  const desktopCount=records.length>=7?7:Math.min(5,records.length),mobileCount=Math.min(3,records.length);
  swiper=new window.Swiper(stage,{initialSlide:activeIndex,slidesPerView:compact?mobileCount:desktopCount,centeredSlides:true,centeredSlidesBounds:false,centerInsufficientSlides:true,spaceBetween:compact?2:8,speed:reduceMotion?0:520,freeMode:{enabled:!reduceMotion,sticky:true,momentum:true,momentumRatio:.72,momentumVelocityRatio:.64},resistance:true,resistanceRatio:.7,threshold:6,touchAngle:compact?42:50,followFinger:true,grabCursor:true,simulateTouch:true,watchOverflow:true,watchSlidesProgress:true,preventClicks:true,preventClicksPropagation:true,keyboard:{enabled:false},a11y:{enabled:true,slideRole:null,prevSlideMessage:'이전 사진',nextSlideMessage:'다음 사진'},on:{init(instance){sync(instance.activeIndex);paint(instance)},setTranslate(instance){paint(instance)},progress(instance){paint(instance)},activeIndexChange(instance){sync(instance.activeIndex)},slideChangeTransitionEnd(instance){paint(instance)},resize(instance){paint(instance)}}});
 }else{track.style.overflowX='auto';track.style.scrollSnapType='x mandatory';slides.forEach(slide=>slide.style.scrollSnapAlign='center');sync(activeIndex);paint(null)}
 const resizeObserver=typeof ResizeObserver==='function'?new ResizeObserver(()=>paint(swiper)):null;resizeObserver?.observe(host);
 const controller={destroy(){if(destroyed)return;destroyed=true;abort.abort();resizeObserver?.disconnect();if(swiper){swiper.destroy(true,true);swiper=null}host.replaceChildren();host.classList.remove('photos-spatial','photos-spatial--compact','photos-spatial--empty');delete host.__photosStorySpatial},getActiveTile(){return records[activeIndex]?.tile||null}};
 host.__photosStorySpatial=controller;return controller;
}
window.PhotosStorySpatial=Object.freeze({mount});
})();
"""
