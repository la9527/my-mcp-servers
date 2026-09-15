// Read-only browser fixtures; no Story form submit, DB write or photo export.
import {execFileSync} from 'node:child_process';
const session='photos-book-edge-cases';
const browser=(...args)=>{
  const result=execFileSync('/opt/homebrew/bin/npx',['--yes','agent-browser','--session',session,'--json',...args],{encoding:'utf8',maxBuffer:10*1024*1024});
  const parsed=JSON.parse(result);if(!parsed.success)throw Error(JSON.stringify(parsed));return parsed.data;
};
const evaluate=script=>{
  const result=execFileSync('/opt/homebrew/bin/npx',['--yes','agent-browser','--session',session,'--json','eval','--stdin'],{input:script,encoding:'utf8',maxBuffer:10*1024*1024});
  const parsed=JSON.parse(result);if(!parsed.success)throw Error(JSON.stringify(parsed));return parsed.data.result;
};
const results=[];
for(const [w,h] of [[1440,1000],[390,844],[844,390],[320,568]]){
  browser('set','viewport',String(w),String(h));
  for(const [name,count,long] of [['single',1,false],['two',2,false],['mixed',9,false],['long',9,true]]){
    browser('open','http://127.0.0.1:18809/preview?theme=memory_volume&date=2026-08-14');
    const setup=evaluate(`(()=>{
      const tiles=[...document.querySelectorAll('[data-photo]')].slice(0,${count}).map(t=>t.cloneNode(true));
      const chapter=document.createElement('section');chapter.className='chapter';chapter.hidden=true;
      const title=document.createElement('h2');title.textContent=${JSON.stringify(long?'날짜와 장소와 사람들의 이야기가 길게 이어지는 기억 '.repeat(18):'함께 걸었던 하루')};
      const paragraph=document.createElement('p');paragraph.className='chapter-copy';paragraph.textContent=${JSON.stringify(long?'사진과 이야기의 모든 문장을 빠뜨리지 않고 책장으로 나눠서 읽을 수 있어야 합니다. '.repeat(70):'사진 속 순간을 한 장씩 다시 펼쳐봅니다.')};
      chapter.append(title,paragraph,...tiles);const host=document.createElement('section');host.style.cssText='max-width:1160px;margin:0 auto;padding:18px;box-sizing:border-box';
      document.body.replaceChildren(host,chapter);
      window.bookFixture=PhotosStoryBook.mount({host,tiles,title:'책 페이지 검증',intro:'브라우저 전용 검증 데이터',compact:innerWidth<768,reduceMotion:true,onOpen:()=>{}});
      window.bookExpectedText=(title.textContent+paragraph.textContent).replace(/\\s/g,'');
      return {pages:Number(document.querySelector('.book-stage').dataset.bookTotal),expected:tiles.map(t=>t.dataset.preview)};
    })()`);
    const inspected=evaluate(`(()=>{const found=new Set(),seenPages=new Set(),overflow=[];let text='';for(let i=0;i<${setup.pages};i++){
      bookFixture.goToPage(i);
      for(const leaf of document.querySelectorAll('.book-bed>.book-leaf')){
        if(!seenPages.has(leaf.dataset.bookPage)){seenPages.add(leaf.dataset.bookPage);for(const e of leaf.querySelectorAll('h2,.book-paragraph'))text+=e.textContent;}
        for(const button of leaf.querySelectorAll('[data-book-photo]'))found.add(button.dataset.bookPhoto);
        const r=leaf.getBoundingClientRect();for(const child of leaf.children){const c=child.getBoundingClientRect();if(c.bottom>r.bottom+1||c.right>r.right+1)overflow.push({page:leaf.dataset.bookPage,cls:child.className,delta:Math.ceil(c.bottom-r.bottom)});}
      }
    }return {found:found.size,textPreserved:text.replace(/\\s/g,'').includes(window.bookExpectedText),overflow,documentOverflow:document.documentElement.scrollWidth>innerWidth,page:Number(document.querySelector('.book-stage').dataset.bookCurrent)};})()`);
    results.push({name,w,h,pages:setup.pages,expected:setup.expected.length,...inspected});
    if(name==='long'||name==='two')browser('screenshot',`/tmp/photos-book-r4-${name}-${w}x${h}.png`);
  }
}
console.log(JSON.stringify(results,null,2));
browser('close');
if(results.some(r=>r.found!==r.expected||!r.textPreserved||r.overflow.length||r.documentOverflow))process.exitCode=1;
