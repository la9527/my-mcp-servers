// Inspect real photographs without saving presentation settings or running analysis.
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
const session='story-theme-polish-check';
const origin=process.env.STORY_PREVIEW_URL||'http://127.0.0.1:18809/preview';
const results=[];
function call(...args){const r=JSON.parse(execFileSync('/opt/homebrew/bin/npx',['--yes','agent-browser','--session',session,'--json',...args],{encoding:'utf8',timeout:45000,maxBuffer:4*1024*1024}));assert.ok(r.success,r.error);return r.data;}
const read=code=>call('eval',code).result;
const ready=()=>call('wait','--fn',`[...document.querySelectorAll('.photos-spatial__slide[aria-current="true"] img,.book-bed img')].every(i=>i.complete&&i.naturalWidth>0)`);
try{
 for(const [width,height] of [[1278,644],[1440,1000],[390,844],[844,390]]){
  call('set','viewport',String(width),String(height));
  for(const theme of ['spatial_ribbon','memory_volume']){
   call('open',origin+'?theme='+theme+'&date=2026-08-14');ready();
   const initial=read(`(()=>{const footer=document.querySelector('.photos-spatial__footer,.book-navigation').getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth,footerBottom:footer.bottom,scroll:scrollY}})()`);
   assert.equal(initial.overflow,false);assert.equal(initial.scroll,0);
   if(height>=644)assert.ok(initial.footerBottom<=height,`${theme} controls outside ${width}×${height}: ${initial.footerBottom}`);
   const screenshot=`/tmp/photos-polish-${theme}-${width}.png`;call('screenshot',screenshot);
   if(theme==='spatial_ribbon'){
    call('focus','.photos-spatial__stage');call('press','ArrowRight');
    call('wait','--fn',`document.querySelector('.photos-spatial__counter').textContent.startsWith('2 /')`);
    call('press','End');call('wait','--fn',`document.querySelector('.photos-spatial__nav--next').disabled`);ready();
    const end=read(`({balance:parseFloat(document.querySelector('.photos-spatial__track').style.left),photo:document.querySelector('.photos-spatial__slide[aria-current="true"]').dataset.index})`);
    assert.ok(end.balance>0);assert.equal(end.photo,'35');
    call('screenshot',`/tmp/photos-polish-spatial-last-${width}.png`);
    call('press','Home');call('press','ArrowRight');call('press','ArrowRight');call('press','ArrowRight');ready();
    call('screenshot',`/tmp/photos-polish-spatial-middle-${width}.png`);
    call('click','.photos-spatial__slide[aria-current="true"]');
   }else{
    call('click','.book-navigation button:last-child');
    call('wait','--fn',`!document.querySelector('.book-stage.is-turning')&&Number(document.querySelector('.book-stage').dataset.bookCurrent)>0`);ready();
    const geometry=read(`(()=>{const out=[];for(const leaf of document.querySelectorAll('.book-bed>.book-leaf')){const r=leaf.getBoundingClientRect();for(const c of leaf.children){const b=c.getBoundingClientRect();if(b.bottom>r.bottom+1||b.right>r.right+1)out.push(c.className)}}return out})()`);
    assert.deepEqual(geometry,[],'Book leaf overflow');
    // A photo may be only partly visible after navigating a short landscape
    // screen. Bring the complete target into view before the driver clicks its centre.
    call('scrollintoview','.book-bed .book-leaf:first-child .book-photo:first-child [data-book-photo]');
    call('click','.book-bed .book-leaf:first-child .book-photo:first-child [data-book-photo]');
   }
   call('wait','--fn',`document.querySelector('[data-viewer]').open`);
   call('wait','--fn',`(()=>{const i=document.querySelector('.swiper-slide-active .swiper-zoom-container img');return !!i?.naturalWidth})()`);
   const fit=read(`(()=>{const s=document.querySelector('[data-viewer] .stage').getBoundingClientRect(),i=document.querySelector('.swiper-slide-active .swiper-zoom-container img').getBoundingClientRect();return i.width<=s.width+1&&i.height<=s.height+1})()`);
   assert.ok(fit,'Viewer photo must fit');call('press','Escape');
   call('wait','--fn',`!document.querySelector('[data-viewer]').open`);
   results.push({theme,width,height,...initial,screenshot,viewerFits:fit});
  }
 }
 console.log(JSON.stringify(results,null,2));
}finally{call('close');}
