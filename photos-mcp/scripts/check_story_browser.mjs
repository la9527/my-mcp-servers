// Behavioral smoke checks against scripts/preview_story_design.py.
// All browser interactions use an isolated agent-browser session. No Story saves.
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';

const origin=process.env.STORY_PREVIEW_URL||'http://127.0.0.1:18809/preview';
const session=process.env.STORY_BROWSER_SESSION||'photos-story-validation';
function browser(...args){
 const raw=execFileSync('/opt/homebrew/bin/npx',['--yes','agent-browser','--session',session,'--json',...args],{encoding:'utf8',maxBuffer:8*1024*1024});
 const result=JSON.parse(raw);assert.equal(result.success,true,result.error);return result.data;
}
function read(js){return browser('eval',js).result}
const pause=()=>new Promise(resolve=>setTimeout(resolve,450));
const reports=[];
const themes=['quiet_memories','moment_clusters','scroll_cinema','spatial_ribbon','memory_volume'];
const openSelectors={quiet_memories:'.psg-quiet-main',moment_clusters:'.psg-cluster-grid button:first-child',scroll_cinema:'[data-cover-open]',spatial_ribbon:'.photos-spatial__slide[aria-current="true"]',memory_volume:'.memory-volume button[data-book-photo]'};
for(const width of (process.env.STORY_TEST_WIDTH?process.env.STORY_TEST_WIDTH.split(',').map(Number):[1440,390])){
 for(const theme of (process.env.STORY_TEST_THEME?process.env.STORY_TEST_THEME.split(','):themes)){
  console.error('Checking',width,theme);
  browser('set','viewport',String(width),width===390?'844':'1000');
  browser('set','media','light');browser('open',origin+'?theme='+theme+'&date=2026-08-14');
  assert.equal(read('document.documentElement.scrollWidth<=innerWidth'),true,'Horizontal overflow');
  const total=read('document.querySelectorAll("[data-photo]").length');assert.ok(total>1);
  browser('wait','--fn',`[...document.querySelectorAll('.story-layout-host img,.psg-gallery img,.photos-spatial img,.story-cover img')].filter(i=>{const r=i.getBoundingClientRect();return r.width>0&&r.top<innerHeight&&r.bottom>0&&r.left<innerWidth&&r.right>0}).every(i=>i.complete)`);
  if(theme==='spatial_ribbon')assert.ok(read('Boolean(document.querySelector(".photos-spatial__stage").swiper)'));
  browser('click',openSelectors[theme]);
  browser('wait','--fn','document.querySelector("[data-viewer]").open');
  browser('wait','--fn','document.querySelector("[data-swiper]").swiper?.slides[0]?.querySelector("img")?.naturalWidth>0');
  await pause();
  assert.equal(read(`(()=>{const s=document.querySelector('[data-swiper]'),i=s.swiper.slides[s.swiper.activeIndex].querySelector('img'),r=i.getBoundingClientRect();return r.width<=s.clientWidth+1&&r.height<=s.clientHeight+1})()`),true,'Photo must fit');
  assert.equal(read('document.querySelector("[data-zoom-reset]").hidden'),true);
  const viewerA11y=browser('a11y','--tags','wcag2a,wcag2aa');
  assert.equal(viewerA11y.counts.violations,0);
  // axe can report an indeterminate backdrop behind a native modal over Coverflow.
  // Keep all semantic findings strict; actual viewer colors are reviewed separately.
  assert.ok(viewerA11y.incomplete.every(item=>item.id==='color-contrast'));
  browser('dblclick','.stage .swiper-slide-active img');await pause();
  assert.ok(read('document.querySelector("[data-swiper]").swiper.zoom.scale')>1,'Double click zoom');
  browser('press','ArrowRight');await pause();
  assert.equal(read('document.querySelector("[data-swiper]").swiper.activeIndex'),1);
  assert.equal(read('document.querySelector("[data-swiper]").swiper.zoom.scale'),1);
  const rect=read('(()=>{const r=document.querySelector("[data-swiper]").getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})()');
  const y=Math.round(rect.y+rect.h/2),start=Math.round(rect.x+rect.w*.8),end=Math.round(rect.x+rect.w*.2);
  browser('mouse','move',String(start),String(y));browser('mouse','down');
  for(let step=1;step<=6;step++)browser('mouse','move',String(Math.round(start+(end-start)*step/6)),String(y));
  browser('mouse','up');await pause();
  assert.equal(read('document.querySelector("[data-swiper]").swiper.activeIndex'),2,'Drag advances one photo');
  assert.ok(read('document.querySelectorAll("[data-filmstrip] button").length')<=11);
  browser('click','[data-grid-toggle]');
  assert.equal(read('document.querySelectorAll("[data-all-grid] button").length'),total);
  const gridA11y=browser('a11y','--tags','wcag2a,wcag2aa');
  assert.equal(gridA11y.counts.violations,0);
  assert.ok(gridA11y.incomplete.every(item=>item.id==='color-contrast'));
  const gridOverflow=read('document.querySelector("[data-all-grid]").scrollHeight>document.querySelector("[data-all-grid]").clientHeight');
  browser('click','[data-all-grid] button:last-child');await pause();
  assert.equal(read('document.querySelector("[data-swiper]").swiper.activeIndex'),total-1);
  browser('click','[data-grid-toggle]');
  if(gridOverflow)browser('wait','--fn','document.querySelector("[data-all-grid]").scrollTop>0');
  browser('click','[data-grid-toggle]');await pause();
  assert.equal(read('document.querySelector("[data-swiper]").swiper.activeIndex'),total-1);
  browser('back');await pause();
  assert.equal(read('document.querySelector("[data-viewer]").open'),false);
  assert.equal(read('document.querySelectorAll("[data-swiper-wrapper] img").length'),0);
  browser('click','[data-info-open]');
  browser('wait','--fn','document.querySelector("[data-story-info]").open');
  browser('click','.presentation-panel > summary');
  browser('wait','--fn','document.querySelector(".presentation-panel").open');
  for(const next of [...themes,...themes].reverse()){
   browser('click',`[data-choice="${next}"]`);await pause();
   assert.equal(read('document.documentElement.dataset.storyTheme'),next);
   assert.equal(read('document.documentElement.scrollWidth<=innerWidth'),true);
   assert.equal(read('document.querySelectorAll(".chapter-details").length'),read('document.querySelectorAll(".story-content .chapter-details,.theme-place-information .chapter-details").length'));
  }
  assert.equal(read('document.querySelectorAll(".swiper-wrapper .swiper-wrapper").length'),0);
  const filterCount=read('document.querySelectorAll("[data-person-filter^=pf_]").length');
  if(filterCount){
   browser('click','[data-person-filter^=pf_]');
   assert.equal(read(`(()=>{const h=document.querySelector('[data-person-filter][aria-pressed=true]').dataset.personFilter;return [...document.querySelectorAll('[data-photo]')].filter(t=>!t.hidden).every(t=>t.dataset.personFacets.split(' ').includes(h))})()`),true);
   browser('click','[data-person-filter=""]');
   assert.equal(read('document.querySelectorAll("[data-photo]:not([hidden])").length'),total);
  }
  const accessibility=browser('a11y','--tags','wcag2a,wcag2aa');
  assert.equal(accessibility.counts.violations,0,JSON.stringify(accessibility.violations.map(v=>v.id)));
  reports.push({width,theme,photos:total,fit:true,zoom:true,keyboard:true,drag:true,grid:true,back:true,themeSwitch:true,peopleFilter:filterCount>0,accessibilityViolations:0});
  console.error('Passed',width,theme);
 }
}
browser('set','media','dark','reduced-motion');browser('open',origin+'?theme=spatial_ribbon');
assert.equal(read('document.querySelector(".photos-spatial__stage").swiper.params.speed'),0);
for(const mode of ['empty','single']){
 browser('open',origin+'?mode='+mode);assert.equal(read('document.documentElement.scrollWidth<=innerWidth'),true);
 if(mode==='empty')assert.equal(read('document.querySelector("[data-all-photos]").disabled'),true);
 else{browser('click','[data-cover-open]');await pause();assert.equal(read('document.querySelector("[data-count]").textContent'),'1 / 1');browser('press','Escape');await pause()}
}
console.log(JSON.stringify({reports,reducedMotion:true,empty:true,single:true},null,2));
browser('close');
