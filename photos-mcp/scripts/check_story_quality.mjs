// Real-browser visual/geometry evidence; never saves themes or starts analysis.
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
const session='story-quality-gate';
const round=process.env.STORY_REVIEW_ROUND||'1';
const origin=process.env.STORY_PREVIEW_URL||'http://127.0.0.1:18809/preview';
function call(...args){const out=JSON.parse(execFileSync('/opt/homebrew/bin/npx',['--yes','agent-browser','--session',session,'--json',...args],{encoding:'utf8',maxBuffer:8*1024*1024}));assert.ok(out.success,out.error);return out.data}
const read=js=>call('eval',js).result;
const themes=['quiet_memories','moment_clusters','scroll_cinema','spatial_ribbon','memory_volume'];
const evidence=[];
for(const [width,height] of [[1440,1000],[1278,644],[390,844],[844,390]]){
 for(const theme of themes){
  call('set','viewport',String(width),String(height));
  call('open',origin+'?theme='+theme+'&date=2026-08-14');
  call('wait','--fn',"document.querySelector('.story-layout-host:not([hidden]),.story-content')!==null");
  const state=read(`({theme:document.documentElement.dataset.storyTheme,overflow:document.documentElement.scrollWidth>innerWidth,photos:document.querySelectorAll('[data-photo]').length,layout:!!document.querySelector('.story-layout-active'),images:[...document.images].filter(i=>i.getBoundingClientRect().width>0&&i.getBoundingClientRect().top<innerHeight).length})`);
  assert.equal(state.theme,theme);assert.equal(state.overflow,false,theme+' overflow');
  assert.ok(state.photos>0);if(theme!=='scroll_cinema')assert.ok(state.layout,theme+' mounted');
  const screenshot=`/tmp/photos-quality-r${round}-${theme}-${width}.png`;
  call('screenshot',screenshot);evidence.push({width,height,theme,screenshot,...state});
 }
 call('open',origin+'?theme=scroll_cinema');call('click','[data-all-photos]');
 call('wait','--fn','document.querySelectorAll("[data-all-grid] [data-psg-index]").length>0');
 const geometry=read(`(()=>{const buttons=[...document.querySelectorAll('[data-all-grid] button[data-psg-index]')],rects=buttons.map(b=>b.getBoundingClientRect());let overlap=0,occluded=0;for(let i=0;i<rects.length;i++){const a=rects[i];for(let j=i+1;j<rects.length;j++){const b=rects[j];if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>.75&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>.75)overlap++}const x=a.x+a.width/2,y=a.y+a.height/2;if(y>90&&y<innerHeight-5&&x>0&&x<innerWidth&&!buttons[i].contains(document.elementFromPoint(x,y)))occluded++}return{count:buttons.length,overlap,occluded,scrollHeight:document.querySelector('[data-all-grid]').scrollHeight,clientHeight:document.querySelector('[data-all-grid]').clientHeight}})()`);
 assert.equal(geometry.count,read('document.querySelectorAll("[data-photo]").length'));
 assert.equal(geometry.overlap,0,'Photos overlap');assert.equal(geometry.occluded,0,'Photo click target covered');
 assert.ok(geometry.scrollHeight>geometry.clientHeight);
 const screenshot=`/tmp/photos-quality-r${round}-all-${width}.png`;call('screenshot',screenshot);evidence.push({width,theme:'all',screenshot,...geometry});
 call('click','[data-all-grid] button:last-child');
 call('wait','--fn','document.querySelector("[data-swiper]").swiper?.activeIndex===314');
 call('click','[data-grid-toggle]');
 call('wait','--fn','document.querySelector("[data-all-grid]").scrollTop>0');
 const returned=read('document.querySelector("[data-all-grid]").scrollTop');
 assert.ok(returned>0,'315-photo grid must restore the scroll position');
 evidence.push({width,theme:'all-return',scrollTop:returned});
}
console.log(JSON.stringify({round,evidence},null,2));call('close');
