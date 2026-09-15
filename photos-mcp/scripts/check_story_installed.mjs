// Read-only installed-server smoke. Changes only unsaved radio previews.
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
const session='photos-installed-story-smoke';
function browser(...args){const r=JSON.parse(execFileSync('/opt/homebrew/bin/npx',['--yes','agent-browser','--session',session,'--json',...args],{encoding:'utf8',maxBuffer:6*1024*1024}));assert.ok(r.success,r.error);return r.data;}
const read=js=>browser('eval',js).result;
const openers={quiet_memories:'.psg-quiet-main',moment_clusters:'.psg-cluster-grid button',scroll_cinema:'[data-cover-open]',spatial_ribbon:'.photos-spatial__slide[aria-current="true"]',memory_volume:'.memory-volume [data-book-photo]'};
const evidence=[];
for(const width of [1440,390]){
 browser('set','viewport',String(width),width===1440?'1000':'844');
 browser('open','http://127.0.0.1:18791/photos');
 assert.equal(read('document.querySelectorAll("[data-choice]").length'),5);
 assert.equal(read('document.documentElement.dataset.storyTheme'),'scroll_cinema');
 for(const theme of Object.keys(openers)){
  console.error('Installed check',width,theme);
  browser('click','[data-info-open]');
  if(!read('document.querySelector(".presentation-panel").open'))browser('click','.presentation-panel>summary');
  browser('click',`[data-choice="${theme}"]`);
  assert.equal(read('document.documentElement.dataset.storyTheme'),theme);
  const maps=read(`({total:document.querySelectorAll('.chapter-details').length,visible:document.querySelectorAll('${theme==='scroll_cinema'?'.story-content':'.theme-place-information'} .chapter-details').length})`);
  assert.equal(maps.total,maps.visible);
  browser('click','[data-story-info]>summary');read('scrollTo(0,0)');
  assert.equal(read('document.documentElement.scrollWidth>innerWidth'),false);
  browser('click',openers[theme]);
  browser('wait','--fn','document.querySelector("[data-viewer]").open');
  browser('wait','--fn','document.querySelector("[data-swiper]").swiper?.slides[0]?.querySelector("img")?.naturalWidth>0');
  const fitted=read(`(()=>{const s=document.querySelector('[data-swiper]'),r=s.swiper.slides[s.swiper.activeIndex].querySelector('img').getBoundingClientRect();return r.width<=s.clientWidth+1&&r.height<=s.clientHeight+1})()`);
  assert.ok(fitted);
  browser('press','Escape');browser('wait','--fn','!document.querySelector("[data-viewer]").open');
  browser('wait','--fn','!history.state?.photosViewer');
  read('scrollTo(0,0)');
  const screenshot=`/tmp/photos-installed-${theme}-${width}.png`;browser('screenshot',screenshot);
  evidence.push({width,theme,maps,fitted,screenshot});
 }
 // The preview choices must not have persisted to the user's Story settings.
 browser('reload');assert.equal(read('document.documentElement.dataset.storyTheme'),'scroll_cinema');
}
console.log(JSON.stringify({evidence,persistentSettingsUnchanged:true},null,2));browser('close');
