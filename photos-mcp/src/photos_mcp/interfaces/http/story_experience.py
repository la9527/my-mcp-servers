"""Approved photo-first Story layouts and shared, gesture-based photo viewer.

Kept as Python constants so source, standalone Mac and mobile servers package
the exact same assets. Layers: page 0, sticky controls 2, native dialog top layer.
"""

EXPERIENCE_CSS = r"""
html[data-story-theme="scroll_cinema"],html[data-story-theme="spatial_ribbon"]{
  --ink:#242528;--muted:#65666b;--paper:#fafafa;--card:#f0f0f1;
  --line:#dcdcdf;--accent:#343539;--accent2:#ececee;--scrim:#101113;
  color-scheme:light;
}
html[data-story-theme="spatial_ribbon"]{
  --ink:#ececee;--muted:#b0b1b7;--paper:#18191c;--card:#222327;
  --line:#3c3d42;--accent:#ededf0;--accent2:#303136;color-scheme:dark;
}
html[data-story-theme="scroll_cinema"] .shell,
html[data-story-theme="spatial_ribbon"] .shell{width:min(1440px,100%);padding:0 clamp(18px,4vw,64px) 48px}
.story-nav{display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:76px;border-bottom:1px solid var(--line)}
.story-brand{font-size:1rem;font-weight:650;letter-spacing:-.025em;text-decoration:none}
.story-nav-actions{display:flex;gap:4px;align-items:center}
.story-nav button,.story-nav .button{font-size:.82rem;padding:8px 12px;background:transparent;color:var(--ink);font-weight:550;white-space:nowrap;border-radius:8px}
.story-nav button:hover{background:var(--accent2)}
.story-cover{display:grid;grid-template-columns:minmax(230px,.6fr) minmax(0,1.4fr);align-items:center;gap:clamp(28px,5vw,80px);padding:44px 0 56px;min-height:min(760px,calc(100dvh - 76px))}
.cover-copy{min-width:0}.cover-date{font-size:.78rem;color:var(--muted);margin:0 0 20px;line-height:1.8}
html[data-story-theme] .story-cover h1{font-family:ui-serif,"AppleMyungjo","Batang",Georgia,serif;font-size:clamp(1.8rem,3.2vw,3.15rem);font-weight:500;line-height:1.25;letter-spacing:-.04em;max-width:100%;width:auto;word-break:keep-all;overflow-wrap:anywhere;margin:0 0 24px}
.story-cover .lede{font-size:.94rem;line-height:1.9;max-width:36ch;word-break:keep-all;overflow-wrap:anywhere}
.cover-photo{display:block;width:100%;padding:0;border:0;border-radius:0;background:var(--card);line-height:0;cursor:zoom-in;overflow:hidden}
.cover-photo img{display:block;width:100%;height:min(62dvh,640px);object-fit:contain}
.cover-count{color:var(--muted);font-size:.78rem;margin:24px 0 0}
.story-information{border-block:1px solid var(--line);padding:0;margin:0 0 40px}
.story-enhanced .story-information:not([open]){display:none}
.story-information>summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;min-height:52px;font-size:.86rem;font-weight:550}
.story-information>summary::after{content:"+";font-size:1.2rem}.story-information[open]>summary::after{content:"−"}
.story-information>summary::-webkit-details-marker{display:none}.story-information-content{padding:4px 0 24px}
.story-information .meta{margin:10px 0}.story-information .presentation-panel{margin:16px 0}
.story-information .theme-choices{grid-template-columns:repeat(2,minmax(0,1fr))}
.story-information .theme-choice{border-radius:8px;min-height:106px}.story-information .theme-choice::before{display:none}
.story-information .theme-choice strong{font-size:1rem}.story-information .theme-choice span{font-size:.82rem}
.story-information .presentation-panel>summary::after{display:none}
.story-information .person-chip,.story-information .location-chip,.chapter-details .place{background:var(--accent2);color:var(--ink)}
.story-information .person-chip[aria-pressed=true],.chapter-details .place[aria-pressed=true]{background:var(--ink);color:var(--paper)}
.story-information .presentation-action button{background:var(--ink);color:var(--paper)}
.story-information .theme-choice:last-child{grid-column:auto}
.story-content .chapters{gap:96px;margin-top:48px}
.story-content .chapter{border:0;padding:0;scroll-margin-top:24px;min-width:0}
.story-content .chapter-head{margin:0 0 28px;gap:12px}
.story-content .chapter-date{font-size:.78rem;color:var(--muted);letter-spacing:0;font-weight:500}
.story-content .chapter h2{font-family:ui-serif,"AppleMyungjo","Batang",Georgia,serif;font-weight:500;font-size:clamp(1.5rem,2.7vw,2.5rem);line-height:1.4;letter-spacing:-.03em;word-break:keep-all;overflow-wrap:anywhere}
.story-content .chapter-copy{font-size:.94rem;line-height:1.95;word-break:keep-all;overflow-wrap:anywhere}
.story-content .people-caption{color:var(--muted);font-weight:500}.story-content .people-caption::before{display:none}
.chapter-details{margin-top:24px;color:var(--muted);font-size:.85rem}
.chapter-details>summary{cursor:pointer;min-height:44px;display:flex;align-items:center;gap:8px}
.chapter-details>summary::after{content:"+"}.chapter-details[open]>summary::after{content:"−"}
.chapter-details .story-map{border-radius:8px}.chapter-details .story-map iframe{max-height:300px}
.story-content .location-subchapter{margin:0 0 24px;min-width:0}
.story-content .location-subchapter h3{font-size:.78rem;font-weight:500;gap:10px;margin:12px 0;color:var(--muted)}
.story-content .location-subchapter h3 span{display:none}
.story-content .location-subchapter[data-location-unknown] h3{display:none}
.story-content .theme-gallery{min-width:0}
.story-content .grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:0}
.story-content .tile{border-radius:2px;background:var(--card);aspect-ratio:4/3;min-width:0;box-shadow:none}
.story-content .tile img{object-fit:contain}.story-content .tile span{display:none}
.story-content .grid>.tile:first-child{grid-column:1/-1;aspect-ratio:auto}
.story-content .grid>.tile:first-child img{height:clamp(300px,58dvh,640px);object-fit:contain}
html[data-story-theme="scroll_cinema"] .chapter{display:grid;grid-template-columns:minmax(200px,.55fr) minmax(0,1.45fr);gap:0 clamp(24px,5vw,72px);align-items:start}
html[data-story-theme="scroll_cinema"] .chapter-head{grid-column:1;grid-row:1;position:sticky;top:28px}
html[data-story-theme="scroll_cinema"] .chapter>.location-subchapter,
html[data-story-theme="scroll_cinema"] .chapter>.theme-gallery,
html[data-story-theme="scroll_cinema"] .chapter>.chapter-details{grid-column:2}
.gallery-controls{display:none}
html[data-story-theme="spatial_ribbon"] .story-cover{min-height:0;grid-template-columns:1fr;padding:42px 0 26px;gap:0}
html[data-story-theme="spatial_ribbon"] .cover-photo{display:none}
html[data-story-theme="spatial_ribbon"] .story-cover h1{font-size:clamp(1.7rem,3vw,2.6rem);margin:0 0 12px}
html[data-story-theme="spatial_ribbon"] .cover-date{margin-bottom:12px}
html[data-story-theme="spatial_ribbon"] .cover-copy .lede{max-width:65ch}
html[data-story-theme="spatial_ribbon"] .cover-count{margin-top:14px}
html[data-story-theme="spatial_ribbon"] .cover-date,html[data-story-theme="spatial_ribbon"] .cover-count{display:none}
html[data-story-theme="spatial_ribbon"] .story-content .chapters{margin-top:14px;gap:64px}
html[data-story-theme="spatial_ribbon"] .story-content .chapter-date{display:none}
html[data-story-theme="spatial_ribbon"] .story-content .chapter-head{max-width:680px;margin:0 auto 22px;text-align:center}
html[data-story-theme="spatial_ribbon"] .story-content .chapter-copy{margin-inline:auto}
html[data-story-theme="spatial_ribbon"] .story-content .people-caption{justify-content:center}
html[data-story-theme="spatial_ribbon"] .theme-gallery.swiper{overflow:hidden;padding:18px 0 12px;perspective:1200px}
html[data-story-theme="spatial_ribbon"] .theme-gallery.swiper .grid{display:flex;gap:0;margin:0}
html[data-story-theme="spatial_ribbon"] .theme-gallery.swiper .tile{width:min(65%,680px);height:clamp(260px,56dvh,620px);aspect-ratio:auto;background:transparent;cursor:pointer;flex-shrink:0}
html[data-story-theme="spatial_ribbon"] .theme-gallery.swiper .tile img{height:100%;width:100%;object-fit:contain;transform:none}
html[data-story-theme="spatial_ribbon"] .theme-gallery.swiper .tile:not(.swiper-slide-active){opacity:.7}
html[data-story-theme="spatial_ribbon"] .gallery-controls{display:flex;align-items:center;justify-content:center;gap:16px;margin:12px 0 4px}
.gallery-controls button{background:transparent;color:var(--ink);font-size:.82rem;font-weight:500;padding:8px 14px;border-radius:8px}
.gallery-controls button:disabled{opacity:.35;cursor:default}.gallery-count{font-size:.8rem;color:var(--muted);min-width:64px;text-align:center;font-variant-numeric:tabular-nums}
html[data-story-theme="spatial_ribbon"] .theme-pagination{display:none}
.story-content .chapter[hidden],.story-content .location-subchapter[hidden],.story-content .tile[hidden]{display:none!important}
.story-filter-empty{padding:32px 0;color:var(--muted)}.story-filter-empty[hidden]{display:none}
/* The shared viewer always uses a calm neutral mat and fits the complete photo. */
body:has(.viewer[open]){overflow:hidden}
.viewer{background:#101113;color:#f4f4f5;--scrim:#101113;margin:0;inset:0;width:100%;height:100dvh}
.viewer-inner{grid-template-rows:auto minmax(0,1fr) auto auto auto auto}
.viewer-top,.viewer-foot,.gesture-hint,.position-indicator{background:#101113}
.viewer-top{justify-content:space-between;min-height:56px}
.viewer button,.viewer .button{background:transparent;color:#ededf0;backdrop-filter:none;border-radius:8px;font-size:.82rem;font-weight:500;padding:8px 12px}
.viewer button:hover{background:#28292d}.viewer .zoom-control{width:auto;border:1px solid #57585e}
.viewer .zoom-control[hidden]{display:none}
.stage.swiper{background:#101113}
.viewer-foot{max-width:1280px;width:100%;margin:auto;padding-inline:20px;min-height:50px}
.caption strong{font-size:.86rem;font-weight:500}.caption span{font-size:.75rem;color:#b4b5bc}
.position-indicator{padding:8px 20px}.position-track{display:none}.position-fill{background:#e4e4e7}
.position-count{font-size:.8rem;font-weight:500}.gesture-hint{font-size:.68rem;color:#a7a8ae;padding:4px 16px}
.viewer-filmstrip{display:flex;gap:6px;justify-content:center;overflow-x:auto;max-width:100%;padding:8px 16px;scrollbar-width:none;min-height:70px}
.viewer-filmstrip button{flex:0 0 62px;width:62px;height:52px;min-width:0;min-height:0;border:2px solid transparent;padding:2px;border-radius:4px;opacity:.6}
.viewer-filmstrip button[aria-current=true]{border-color:#eeeef1;opacity:1}
.viewer-filmstrip img{width:100%;height:100%;object-fit:cover;border-radius:1px}
.viewer-all-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;overflow:auto;padding:20px;align-content:start;min-height:0}
.viewer-all-grid button{padding:0;border-radius:2px;display:block;aspect-ratio:1;background:#222327}
.viewer-all-grid img{width:100%;height:100%;object-fit:contain;display:block}
.viewer-all-grid[hidden]{display:none}.viewer.is-grid .stage,.viewer.is-grid .viewer-filmstrip,.viewer.is-grid .position-indicator,.viewer.is-grid .gesture-hint,.viewer.is-grid .viewer-foot{display:none}
.viewer-all-grid{display:block;overflow:auto;min-width:0}
.viewer.is-grid .viewer-inner{grid-template-rows:auto minmax(0,1fr)}
.story-layout-host{min-width:0;width:100%}.story-layout-host[hidden]{display:none}
html.story-layout-active .story-cover,html.story-layout-active .story-content{display:none!important}
html[data-story-theme="quiet_memories"],html[data-story-theme="moment_clusters"],html[data-story-theme="memory_volume"]{--ink:#29292b;--muted:#646367;--paper:#f6f5f2;--card:#eeece7;--line:#dedbd5;--accent:#343539;--accent2:#e9e7e2;color-scheme:light}
html[data-story-theme="quiet_memories"] .shell,html[data-story-theme="moment_clusters"] .shell,html[data-story-theme="memory_volume"] .shell{width:min(1440px,100%);padding:0 clamp(18px,4vw,64px) 48px}
.photo-error{position:absolute;inset:auto 16px 20px;text-align:center;color:#dadbe0;font-size:.82rem}
.story-content .tile.image-failed::after,.cover-photo.image-failed::after{content:"사진을 불러오지 못했어요. 눌러 다시 보기";display:block;color:var(--muted);font:500 .85rem/1.6 system-ui;padding:32px}
.story-content .tile.image-failed img,.cover-photo.image-failed img{display:none}
@media(prefers-color-scheme:dark){html[data-story-theme="scroll_cinema"]{--ink:#e6e6e9;--muted:#adaeb5;--paper:#191a1d;--card:#242529;--line:#3d3e44;--accent:#e6e6e9;--accent2:#303136;color-scheme:dark}}
@media(min-width:900px) and (prefers-reduced-motion:no-preference){.story-content .chapter.is-entering .theme-gallery{animation:scene-enter .65s ease both}@keyframes scene-enter{from{opacity:.7;transform:translateY(18px)}to{opacity:1;transform:translateY(0)}}}
@media(max-width:760px){
 .story-nav{min-height:64px}.story-nav button{font-size:.76rem;padding:8px}.story-brand{font-size:.9rem}
 .story-cover{display:flex;flex-direction:column-reverse;align-items:stretch;gap:26px;padding:22px 0 30px;min-height:0}
 .cover-photo img{height:48dvh;min-height:260px}.cover-date{margin-bottom:10px}
 html[data-story-theme] .story-cover h1{font-size:1.8rem;line-height:1.4;word-break:keep-all;margin-bottom:12px;letter-spacing:-.04em;max-width:100%}
 .cover-copy .lede{max-width:100%;font-size:.88rem}.cover-count{margin-top:12px}
 .story-information{margin-bottom:26px}.story-information .theme-choices{grid-template-columns:1fr}
 html[data-story-theme="scroll_cinema"] .chapter{display:block}.story-content .chapter-head{position:static!important;margin-bottom:20px}
 .story-content .chapters{gap:58px;margin-top:30px}.story-content .chapter h2{font-size:1.65rem}.story-content .chapter-copy{font-size:.9rem}
 .story-content .grid{gap:8px}.story-content .grid>.tile:first-child img{height:48dvh;min-height:260px}
 html[data-story-theme="spatial_ribbon"] .story-cover{display:block;padding:20px 0 22px}
 html[data-story-theme="spatial_ribbon"] .story-cover h1{font-size:1.25rem;line-height:1.6;margin-bottom:6px}
 html[data-story-theme="spatial_ribbon"] .story-cover .lede{font-size:.8rem;line-height:1.7}
 html[data-story-theme="spatial_ribbon"] .story-content .chapter h2{font-size:1.2rem}
 html[data-story-theme="spatial_ribbon"] .story-content .chapter-head{gap:4px;margin-bottom:12px}
 html[data-story-theme="spatial_ribbon"] .story-content .chapter-copy{font-size:.8rem;line-height:1.65}
 html[data-story-theme="spatial_ribbon"] .theme-gallery.swiper .tile{width:80%;height:46dvh;min-height:250px}
 .viewer-filmstrip{gap:4px;padding:4px 10px;min-height:56px}.viewer-filmstrip button{flex-basis:44px;width:44px;height:44px}
 .viewer-foot{padding-inline:14px}.viewer-all-grid{grid-template-columns:repeat(3,minmax(0,1fr));padding:12px;gap:6px}
}
@media(max-height:520px){.viewer-filmstrip,.gesture-hint{display:none}.viewer-inner{grid-template-rows:auto minmax(0,1fr) auto auto}.viewer-top{min-height:44px}.viewer-foot{min-height:36px;padding-block:4px}}
"""

EXPERIENCE_JS = r"""
(()=>{
'use strict';
const d=document, root=d.documentElement;
root.classList.add('story-enhanced');
const reduceMotion=matchMedia('(prefers-reduced-motion: reduce)');
const compact=matchMedia('(max-width: 760px)');
const presetByTheme={scroll_cinema:'scroll-cinema-v1',spatial_ribbon:'spatial-ribbon-v1',quiet_memories:'quiet-memories-v1',moment_clusters:'moment-clusters-v1',memory_volume:'memory-volume-v1'};
d.querySelectorAll('[data-copy-value]').forEach(button=>button.addEventListener('click',async()=>{
 const original=button.textContent;
 try{await navigator.clipboard.writeText(button.dataset.copyValue||'');button.textContent='복사됨'}catch{button.textContent='복사 실패'}
 setTimeout(()=>{button.textContent=original},1400);
}));
d.querySelectorAll('[data-map-target]').forEach(button=>button.addEventListener('click',()=>{
 const chapter=button.closest('.chapter,[data-theme-place]'),frame=chapter?.querySelector('[data-map-frame]'),link=chapter?.querySelector('[data-map-link]');
 if(frame&&button.dataset.mapTarget){frame.src=button.dataset.mapTarget;frame.title=button.textContent.trim()+' Google 지도'}
 if(link&&button.dataset.mapOpen)link.href=button.dataset.mapOpen;
 chapter?.querySelectorAll('[data-map-target]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
}));
d.querySelectorAll('[data-info-open]').forEach(button=>button.addEventListener('click',()=>{
 const panel=d.querySelector('[data-story-info]');if(!panel)return;
 panel.open=!panel.open;
 if(panel.open){panel.scrollIntoView({block:'start',behavior:'instant'});panel.querySelector('summary')?.focus({preventScroll:true})}
}));
d.querySelectorAll('.tile img,.cover-photo img').forEach(image=>image.addEventListener('error',()=>image.parentElement.classList.add('image-failed')));
if('IntersectionObserver' in window){
 const observer=new IntersectionObserver(entries=>entries.forEach(entry=>{
  if(entry.isIntersecting){entry.target.classList.add('is-entering');observer.unobserve(entry.target)}
 }),{threshold:.05});
 d.querySelectorAll('.chapter').forEach(chapter=>observer.observe(chapter));
}
let themeSwipers=[],activeLayout=null,rememberedTile=null;
const sourceContent=d.querySelector('.story-content'),layoutHost=d.createElement('section');
layoutHost.className='story-layout-host';layoutHost.hidden=true;layoutHost.setAttribute('aria-label','Story 감상');
sourceContent?.before(layoutHost);
// Keep the original map nodes (and listeners) accessible when a theme hides the
// source chapters. Never duplicate private map URLs or initialize hidden iframes.
const placesHost=d.createElement('section');placesHost.className='theme-place-information';placesHost.hidden=true;
d.querySelector('.story-information-content')?.append(placesHost);
const placeSections=[...d.querySelectorAll('.story-content .chapter-details')].map(details=>{
 const chapter=details.closest('.chapter'),anchor=d.createComment('chapter-place-position');details.before(anchor);
 const group=d.createElement('article');group.dataset.themePlace='';
 const heading=d.createElement('h3');heading.textContent=chapter?.querySelector('h2')?.textContent||'촬영 장소';group.append(heading);
 return{details,chapter,anchor,group};
});
function restorePlaces(){
 placeSections.forEach(item=>item.anchor.after(item.details));placesHost.replaceChildren();placesHost.hidden=true;
}
function showThemePlaces(){
 placeSections.filter(item=>!item.chapter?.hidden).forEach(item=>{item.group.append(item.details);placesHost.append(item.group)});
 placesHost.hidden=!placesHost.children.length;
}
const positions=new WeakMap();
function releaseThemeGalleries(){
 restorePlaces();
 if(activeLayout){rememberedTile=activeLayout.getActiveTile?.()||rememberedTile;activeLayout.destroy();activeLayout=null}
 layoutHost.replaceChildren();layoutHost.hidden=true;root.classList.remove('story-layout-active');
 themeSwipers.forEach(instance=>{positions.set(instance.el,instance.slides[instance.activeIndex]);instance.destroy(true,true)});
 themeSwipers=[];
 d.querySelectorAll('[data-theme-gallery]').forEach(shell=>{
  const track=shell.querySelector('[data-theme-gallery-track]');shell.classList.remove('swiper');track?.classList.remove('swiper-wrapper');
  track?.querySelectorAll('[data-photo]').forEach(tile=>{tile.classList.remove('swiper-slide');tile.removeAttribute('style')});
 });
}
function initThemeGalleries(preferredTile=null){
 releaseThemeGalleries();
 if(preferredTile instanceof Element&&tiles.includes(preferredTile))rememberedTile=preferredTile;
 const theme=root.dataset.storyTheme;
 const module=theme==='memory_volume'?window.PhotosStoryBook:theme==='spatial_ribbon'?window.PhotosStorySpatial:['quiet_memories','moment_clusters'].includes(theme)?window.PhotosStoryGallery:null;
 if(module&&tiles.length){
  showThemePlaces();
  layoutHost.hidden=false;root.classList.add('story-layout-active');
  activeLayout=module.mount({host:layoutHost,tiles,mode:theme,reduceMotion:reduceMotion.matches,compact:compact.matches,
   title:d.querySelector('.story-cover h1')?.textContent||'',intro:d.querySelector('.story-cover .lede')?.textContent||'',
   initialTile:tiles.includes(rememberedTile)?rememberedTile:tiles[0],
   onOpen(tile){const index=tiles.indexOf(tile);if(index>=0){rememberedTile=tile;openViewer(index,false,d.activeElement)}}});
  return;
 }
 d.querySelectorAll('[data-theme-gallery-track]').forEach(track=>{
  const first=[...track.querySelectorAll('[data-photo]')].find(tile=>!tile.hidden);
  if(first)first.querySelector('img').src=first.dataset.preview;
 });
 if(!['spatial_ribbon','cinema','map_journey'].includes(theme)||typeof window.Swiper!=='function')return;
 d.querySelectorAll('[data-theme-gallery]').forEach(shell=>{
  const track=shell.querySelector('[data-theme-gallery-track]');
  const slides=[...(track?.querySelectorAll('[data-photo]')||[])].filter(tile=>!tile.hidden);
  if(!track||!slides.length)return;
  shell.classList.add('swiper');track.classList.add('swiper-wrapper');slides.forEach(tile=>tile.classList.add('swiper-slide'));
  const spatial=theme==='spatial_ribbon';
  const sync=instance=>{
   const label=shell.querySelector('[data-gallery-count]');if(label)label.textContent=(instance.activeIndex+1)+' / '+slides.length;
   const prev=shell.querySelector('[data-gallery-prev]'),next=shell.querySelector('[data-gallery-next]');
   if(prev)prev.disabled=instance.isBeginning;if(next)next.disabled=instance.isEnd;
   slides.forEach((tile,index)=>tile.tabIndex=spatial&&Math.abs(index-instance.activeIndex)>1?-1:0);
   if(spatial)slides.slice(Math.max(0,instance.activeIndex-1),instance.activeIndex+2).forEach(tile=>{tile.querySelector('img').src=tile.dataset.preview});
  };
  const instance=new window.Swiper(shell,{
   initialSlide:Math.max(0,slides.indexOf(positions.get(shell))),slidesPerView:'auto',centeredSlides:spatial,
   effect:spatial&&!reduceMotion.matches?'coverflow':'slide',
   coverflowEffect:{rotate:compact.matches?8:14,stretch:compact.matches?-12:-30,depth:110,modifier:1,slideShadows:false},
   freeMode:{enabled:!spatial},speed:reduceMotion.matches?0:460,
   resistanceRatio:.65,threshold:8,grabCursor:true,watchOverflow:true,
   keyboard:{enabled:false},mousewheel:{forceToAxis:true,releaseOnEdges:true},
   a11y:{enabled:true,prevSlideMessage:'이전 사진',nextSlideMessage:'다음 사진'},
   on:{init:sync,slideChange:sync}
  });
  themeSwipers.push(instance);
 });
}
d.querySelectorAll('[data-gallery-prev],[data-gallery-next]').forEach(button=>button.addEventListener('click',()=>{
 const instance=button.closest('[data-theme-gallery]')?.swiper;
 if(button.hasAttribute('data-gallery-prev'))instance?.slidePrev();else instance?.slideNext();
}));
d.querySelectorAll('[data-choice] input').forEach(input=>input.addEventListener('change',()=>{
 if(!input.checked)return;root.dataset.storyTheme=input.value;root.dataset.designPreset=presetByTheme[input.value]||'';
 const label=d.querySelector('[data-selected-theme]');if(label)label.textContent=input.closest('label').querySelector('strong').textContent;
 initThemeGalleries();
}));
reduceMotion.addEventListener('change',initThemeGalleries);compact.addEventListener('change',initThemeGalleries);

const dialog=d.querySelector('[data-viewer]');if(!dialog)return;
const allTiles=[...d.querySelectorAll('[data-photo]')];let tiles=[...allTiles];
const filterButtons=[...d.querySelectorAll('[data-person-filter]')],filterStatus=d.querySelector('[data-person-filter-status]');
function applyPersonFilter(handle,label){
 releaseThemeGalleries();
 allTiles.forEach(tile=>{tile.hidden=Boolean(handle)&&!(tile.dataset.personFacets||'').split(' ').includes(handle);tile.tabIndex=0});
 tiles=allTiles.filter(tile=>!tile.hidden);
 d.querySelectorAll('.location-subchapter,.chapter').forEach(group=>{group.hidden=![...group.querySelectorAll('[data-photo]')].some(tile=>!tile.hidden)});
 filterButtons.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.personFilter===handle)));
 if(filterStatus)filterStatus.textContent=(handle?label+' 사진 ':'전체 사진 ')+tiles.length+'장';
 const empty=d.querySelector('[data-filter-empty]');if(empty)empty.hidden=tiles.length>0;
 if(dialog.open)dialog.close();initThemeGalleries();
}
filterButtons.forEach(button=>button.addEventListener('click',()=>applyPersonFilter(button.dataset.personFilter||'',button.dataset.personLabel||'선택한 인물')));
const stage=dialog.querySelector('[data-swiper]'),wrapper=dialog.querySelector('[data-swiper-wrapper]');
const count=dialog.querySelector('[data-count]'),positionTrack=dialog.querySelector('[data-position-progress]'),positionFill=dialog.querySelector('[data-position-fill]');
const title=dialog.querySelector('[data-title]'),detail=dialog.querySelector('[data-detail]'),download=dialog.querySelector('[data-save]'),zoomReset=dialog.querySelector('[data-zoom-reset]');
const strip=dialog.querySelector('[data-filmstrip]'),allGrid=dialog.querySelector('[data-all-grid]'),gridToggle=dialog.querySelector('[data-grid-toggle]');
let swiper=null,resizeFrame=0,lastFocus=null,indexNow=0,viewerEpoch=0,lastDoubleTap=0,gridLayout=null,gridScrollTop=0;
const marker='photos-viewer-'+Math.random().toString(36).slice(2);
function activeImage(instance=swiper){return instance?.slides?.[instance.activeIndex]?.querySelector('img')||null}
function fitImage(image){
 const naturalWidth=image?.naturalWidth||0,naturalHeight=image?.naturalHeight||0,availableWidth=stage.clientWidth,availableHeight=stage.clientHeight;
 if(!naturalWidth||!naturalHeight||!availableWidth||!availableHeight)return false;
 const ratio=Math.min(availableWidth/naturalWidth,availableHeight/naturalHeight);
 image.style.width=naturalWidth*ratio+'px';image.style.height=naturalHeight*ratio+'px';return true;
}
function updateZoom(scale=1){
 zoomReset.hidden=scale<=1.01;zoomReset.textContent='화면에 맞춤';zoomReset.setAttribute('aria-label','현재 '+scale.toFixed(1)+'배, 화면에 맞춤');
}
function resetZoom(){if(swiper?.zoom?.scale>1)swiper.zoom.out();updateZoom(1)}
function go(index){resetZoom();if(swiper)swiper.slideTo(index);else{indexNow=index;buildViewer(index)}}
function thumb(tile,index){
 const button=d.createElement('button');button.type='button';button.setAttribute('aria-label',(index+1)+'번째 사진 보기');
 const image=d.createElement('img');image.src=tile.querySelector('img').getAttribute('src');image.alt=tile.dataset.alt||'';image.loading='lazy';
 button.append(image);return button;
}
function renderStrip(index){
 const fragment=d.createDocumentFragment(),radius=compact.matches?3:5;
 for(let i=Math.max(0,index-radius);i<=Math.min(tiles.length-1,index+radius);i++){
  const button=thumb(tiles[i],i);button.setAttribute('aria-current',String(i===index));button.addEventListener('click',()=>go(i));fragment.append(button);
 }
 strip.replaceChildren(fragment);
}
function updateMeta(instance=swiper){
 if(!tiles.length)return;const index=instance?.activeIndex??indexNow,t=tiles[index];indexNow=index;
 count.textContent=(index+1)+' / '+tiles.length;
 positionTrack.setAttribute('aria-valuemax',String(tiles.length));positionTrack.setAttribute('aria-valuenow',String(index+1));positionFill.style.width=(index+1)/tiles.length*100+'%';
 title.textContent=t.dataset.title||'사진';detail.textContent=[t.dataset.date,t.dataset.location,t.dataset.people].filter(Boolean).join(' · ');
 download.hidden=!t.dataset.download;if(t.dataset.download){download.href=t.dataset.download;download.setAttribute('download','')}else download.removeAttribute('href');
 renderStrip(index);
}
function ensureImage(index,instance=swiper){
 if(!instance||!tiles.length)return;
 const normalized=(index+tiles.length)%tiles.length,slide=instance.slides[normalized],image=slide?.querySelector('img');
 if(!image||image.getAttribute('src'))return;
 slide.classList.add('is-loading');
 image.addEventListener('load',()=>{fitImage(image);slide.classList.remove('is-loading')},{once:true});
 image.addEventListener('error',()=>{
  slide.classList.remove('is-loading');const error=d.createElement('span');error.className='photo-error';error.textContent='사진을 불러오지 못했어요. 닫은 뒤 다시 열어 주세요.';slide.append(error);
 },{once:true});
 image.src=tiles[normalized].dataset.preview||'';
 if(image.complete&&image.naturalWidth){fitImage(image);slide.classList.remove('is-loading')}
}
function loadNearby(instance=swiper){if(instance)[-1,0,1].forEach(offset=>ensureImage(instance.activeIndex+offset,instance))}
function releaseViewer(){
 if(swiper){swiper.destroy(true,true);swiper=null}
 cancelAnimationFrame(resizeFrame);wrapper.replaceChildren();strip.replaceChildren();updateZoom(1);
}
function fitVisible(){
 if(!swiper)return;swiper.slides.forEach(slide=>{const image=slide.querySelector('img');if(image?.naturalWidth)fitImage(image)});resetZoom();
}
function buildViewer(startIndex){
 releaseViewer();if(!tiles.length)return;indexNow=startIndex;
 const fragment=d.createDocumentFragment();
 tiles.forEach((tile,index)=>{
  const slide=d.createElement('div');slide.className='swiper-slide';slide.setAttribute('role','group');slide.setAttribute('aria-label',(index+1)+' / '+tiles.length);
  const zoom=d.createElement('div');zoom.className='swiper-zoom-container';zoom.dataset.swiperZoom='4';
  const image=d.createElement('img');image.alt=tile.dataset.alt||'';image.decoding='async';zoom.append(image);slide.append(zoom);fragment.append(slide);
 });wrapper.append(fragment);
 if(typeof window.Swiper!=='function'){
  [...wrapper.children].forEach((slide,index)=>{slide.hidden=index!==startIndex;if(index!==startIndex)slide.style.display='none'});
  const image=wrapper.children[startIndex].querySelector('img');image.addEventListener('load',()=>fitImage(image),{once:true});image.src=tiles[startIndex].dataset.preview;
  updateMeta();return;
 }
 swiper=new window.Swiper(stage,{
  initialSlide:startIndex,speed:reduceMotion.matches?0:360,rewind:true,threshold:6,touchAngle:55,followFinger:true,
  longSwipesMs:450,longSwipesRatio:.18,shortSwipes:true,grabCursor:true,watchOverflow:true,
  keyboard:{enabled:false},zoom:{enabled:true,maxRatio:4,minRatio:1,toggle:true},
  a11y:{enabled:true,prevSlideMessage:'이전 사진',nextSlideMessage:'다음 사진'},
  on:{
   init(instance){updateMeta(instance);loadNearby(instance)},
   slideChangeTransitionStart(instance){if(instance.zoom?.scale>1)instance.zoom.out();updateZoom(1)},
   slideChange(instance){updateMeta(instance);loadNearby(instance)},
   slideChangeTransitionEnd(instance){const image=activeImage(instance);if(image)fitImage(image)},
   zoomChange(_instance,scale){updateZoom(scale)},
   doubleTap(){lastDoubleTap=performance.now()},
   resize(){cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(fitVisible)}
  }
 });
}
function showGrid(){
 resetZoom();dialog.classList.add('is-grid');allGrid.hidden=false;gridToggle.textContent='사진으로 돌아가기';
 if(window.PhotosStoryGallery){
  gridLayout?.destroy();allGrid.replaceChildren();
  gridLayout=window.PhotosStoryGallery.mount({host:allGrid,tiles,mode:'all',reduceMotion:reduceMotion.matches,compact:compact.matches,
   onOpen(tile){showPhoto(tiles.indexOf(tile))}});
  requestAnimationFrame(()=>{allGrid.scrollTop=gridScrollTop});return;
 }
 const fragment=d.createDocumentFragment();tiles.forEach((tile,index)=>{
  const button=thumb(tile,index);button.addEventListener('click',()=>showPhoto(index));fragment.append(button);
 });allGrid.replaceChildren(fragment);allGrid.querySelector('button')?.focus();
}
function showPhoto(index){
 gridScrollTop=allGrid.scrollTop;gridLayout?.destroy();gridLayout=null;
 dialog.classList.remove('is-grid');allGrid.hidden=true;allGrid.replaceChildren();gridToggle.textContent='전체 사진';
 const epoch=viewerEpoch;requestAnimationFrame(()=>{if(dialog.open&&epoch===viewerEpoch){buildViewer(index);gridToggle.focus({preventScroll:true})}});
}
function openViewer(index=0,grid=false,trigger=null){
 if(!tiles.length)return;lastFocus=trigger||d.activeElement;viewerEpoch++;
 dialog.showModal();history.pushState({...history.state,photosViewer:marker},'');
 if(grid)showGrid();else showPhoto(index);
}
allTiles.forEach(tile=>tile.addEventListener('click',()=>{
 const index=tiles.indexOf(tile);if(index<0)return;
 const gallery=tile.closest('[data-theme-gallery]')?.swiper;
 if(root.dataset.storyTheme==='spatial_ribbon'&&gallery&&!tile.classList.contains('swiper-slide-active')){
  gallery.slideTo([...gallery.slides].indexOf(tile));return;
 }
 openViewer(index,false,tile);
}));
d.querySelectorAll('[data-all-photos]').forEach(button=>button.addEventListener('click',()=>openViewer(0,true,button)));
d.querySelectorAll('[data-cover-open]').forEach(button=>button.addEventListener('click',()=>{
 const index=tiles.findIndex(tile=>tile.dataset.preview===button.dataset.preview);
 openViewer(Math.max(0,index),false,button);
}));
gridToggle.addEventListener('click',()=>dialog.classList.contains('is-grid')?showPhoto(indexNow):showGrid());
dialog.querySelector('[data-close]').addEventListener('click',()=>dialog.close());zoomReset.addEventListener('click',resetZoom);
dialog.addEventListener('close',()=>{
 viewerEpoch++;releaseViewer();gridLayout?.destroy();gridLayout=null;allGrid.replaceChildren();dialog.classList.remove('is-grid');
 if(activeLayout&&tiles[indexNow])initThemeGalleries(tiles[indexNow]);
 if(history.state?.photosViewer===marker)history.back();
 if(lastFocus?.isConnected)lastFocus.focus({preventScroll:true});else{layoutHost.tabIndex=-1;layoutHost.focus({preventScroll:true})}
});
window.addEventListener('popstate',()=>{if(dialog.open&&history.state?.photosViewer!==marker)dialog.close()});
window.addEventListener('keydown',event=>{
 if(!dialog.open||dialog.classList.contains('is-grid'))return;
 if(event.key==='0')resetZoom();
 if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
  event.preventDefault();go((indexNow+(event.key==='ArrowRight'?1:-1)+tiles.length)%tiles.length);
 }
});
stage.addEventListener('wheel',event=>{
 if(!swiper)return;event.preventDefault();
 const next=Math.max(1,Math.min(4,(swiper.zoom?.scale||1)*(event.deltaY<0?1.15:1/1.15)));
 if(next<=1.01)swiper.zoom.out();else swiper.zoom.in(next);
},{passive:false});
// Native desktop dblclick can arrive without Swiper's synthetic doubleTap.
// Do not toggle twice when both events are emitted for the same gesture.
stage.addEventListener('dblclick',event=>{
 if(!swiper||performance.now()-lastDoubleTap<350)return;
 if(!event.target.closest('.swiper-slide-active'))return;
 event.preventDefault();swiper.zoom.toggle(event);
});
if('ResizeObserver' in window)new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(fitVisible)}).observe(stage);
initThemeGalleries();
})();
"""
