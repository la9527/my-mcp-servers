"""Physical, two-sided photo-book experience; bounded DOM paper-strip mesh.

The turning surface is geometrically bent, not a rigid rotateY card. Static
pages remain ordinary accessible HTML. No network dependency or photo export.
"""

BOOK_CSS = r"""
.memory-volume{--book-paper:#f7f5f0;--book-ink:#292823;--book-muted:#656259;--book-line:#d3cfc6;color:var(--book-ink);padding:12px 0 34px;min-width:0}
.book-heading{max-width:1160px;margin:12px auto 24px;color:var(--ink)}.book-heading h1{font:500 clamp(1.45rem,2.5vw,2.2rem)/1.45 ui-serif,"AppleMyungjo","Batang",Georgia,serif;letter-spacing:-.035em;margin:0 0 8px;word-break:keep-all;overflow-wrap:anywhere}.book-heading p{font-size:.85rem;line-height:1.8;color:var(--muted);margin:0;max-width:75ch}
.book-toolbar{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:0 auto 20px;max-width:1160px}
.book-toolbar p{font-size:.85rem;color:var(--muted);margin:0}.book-toolbar button,.book-navigation button,.book-toc button{border:0;background:transparent;color:inherit;border-radius:4px;min-height:44px;padding:10px 16px;font:inherit;cursor:pointer}
.book-toolbar button:hover,.book-navigation button:hover,.book-toc button:hover{background:color-mix(in srgb,var(--ink) 8%,transparent)}
.memory-volume button:focus-visible{outline:2px solid var(--ink);outline-offset:4px}
.book-toolbar button{color:var(--ink)}
.book-stage{position:relative;margin:0 auto 26px;perspective:1900px;isolation:isolate;touch-action:pan-y;user-select:none;-webkit-user-select:none;outline:none}
.book-stage::before{content:"";position:absolute;inset:7px -8px -10px;background:#a9a496;border:1px solid #bcb6a7;border-radius:4px 7px 6px 3px;box-shadow:0 20px 26px -15px #332d2666,0 4px 7px #332d2640;z-index:-2}
.book-stage::after{content:"";position:absolute;inset:4px -4px -5px;background:repeating-linear-gradient(0deg,#c4beb0 0 1px,#f4f0e7 1px 3px);border:1px solid #c7c1b5;border-radius:3px;z-index:-1}
.book-bed{position:absolute;inset:0;display:grid;grid-template-columns:1fr 1fr;transform-style:preserve-3d}
.book-leaf{box-sizing:border-box;position:relative;display:flex;flex-direction:column;gap:12px;background:var(--book-paper);color:var(--book-ink);padding:clamp(22px,3vw,40px);height:100%;min-width:0;overflow:hidden;border:1px solid #ded9ce;border-radius:2px;box-shadow:inset 0 0 35px #82786009}
.book-leaf::after{content:"";position:absolute;inset:0;pointer-events:none;opacity:.23;background:repeating-linear-gradient(8deg,transparent 0 3px,#988f7215 3px 4px),repeating-linear-gradient(95deg,transparent 0 7px,#fff9e93b 7px 8px)}
.book-leaf>*{position:relative;z-index:1}.book-leaf::after{z-index:0;opacity:.12}
.book-leaf.is-left{border-radius:3px 0 0 2px;background-image:linear-gradient(90deg,transparent 87%,#a89c8420 96%,#73675048 100%);padding-right:clamp(28px,4vw,52px)}
.book-leaf.is-right{border-radius:0 3px 2px 0;background-image:linear-gradient(90deg,#695f4933 0,#c9c1ad16 5%,transparent 13%);padding-left:clamp(28px,4vw,52px)}
.book-gutter{position:absolute;left:calc(50% - 12px);top:0;bottom:0;width:24px;pointer-events:none;background:linear-gradient(90deg,transparent,#50463025 35%,#f8f5e82b 55%,transparent);z-index:2}
.book-leaf h2{font:500 clamp(1.15rem,2.2vw,1.8rem)/1.5 ui-serif,"AppleMyungjo","Batang",Georgia,serif;letter-spacing:-.035em;margin:0;color:var(--book-ink);overflow-wrap:anywhere;word-break:keep-all}
.book-page-date{font-size:.72rem;color:var(--book-muted);margin:0;line-height:1.5}
.book-paragraph{font:400 clamp(.84rem,1.15vw,.98rem)/1.9 ui-serif,"AppleMyungjo","Batang",Georgia,serif;word-break:keep-all;overflow-wrap:anywhere;margin:0;color:var(--book-ink);white-space:pre-line}
.book-photos{display:grid;grid-template-columns:1fr;gap:14px;min-height:0;flex:1}
.book-photos.is-pair{grid-template-columns:1fr 1fr;gap:12px}
.book-photo{display:flex;flex-direction:column;min-height:0;min-width:0;gap:10px;margin:0}
.book-photo button{display:block;flex:1;min-height:0;width:100%;padding:0;border:0;border-radius:0;background:transparent;cursor:zoom-in;line-height:0;overflow:hidden}
.book-photo img{width:100%;height:100%;max-width:100%;object-fit:contain;display:block;box-sizing:border-box}
.book-photo figcaption{flex:0 0 auto;font-size:.73rem;line-height:1.65;color:var(--book-muted);overflow-wrap:anywhere;word-break:keep-all;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.book-photo figcaption:empty{display:none}.book-folio{font-size:.7rem;text-align:center;margin:0;color:var(--book-muted);font-variant-numeric:tabular-nums}
.book-leaf.is-photo .book-photos{flex:1}.book-leaf.is-story .book-photos{min-height:100px}.book-leaf.is-story .book-paragraph{flex:0 0 auto}.book-leaf.is-text .book-paragraph{margin-top:16px}
.book-navigation{display:flex;justify-content:center;align-items:center;gap:clamp(12px,3vw,42px);color:var(--ink);font-size:.88rem}
.book-navigation button{min-width:84px}.book-navigation button:disabled{opacity:.35;cursor:default}.book-position{font-variant-numeric:tabular-nums;min-width:86px;text-align:center}.book-hint{text-align:center;color:var(--muted);font-size:.74rem;margin:12px 0 0}
.book-toc{max-width:1160px;margin:0 auto 22px;border-block:1px solid var(--line);padding:12px 0;max-height:340px;overflow:auto;color:var(--ink)}
.book-toc[hidden]{display:none}.book-toc button{display:flex;justify-content:space-between;gap:20px;width:100%;text-align:left;font-size:.85rem}.book-toc button span:last-child{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.book-toc button span:first-child{min-width:0;overflow-wrap:anywhere}
.book-turn{position:absolute;inset:0;pointer-events:none;transform-style:preserve-3d;z-index:4}
.book-strip{position:absolute;top:0;transform-style:preserve-3d;transform-origin:left center;will-change:transform}
.book-surface{position:absolute;inset:0;overflow:hidden;backface-visibility:hidden;-webkit-backface-visibility:hidden;background:var(--book-paper)}
.book-surface.back{transform:rotateY(180deg)}
.book-surface .book-leaf{position:absolute;top:0;margin:0;border:0;border-radius:0;max-width:none}
.book-shading{position:absolute;inset:0;pointer-events:none;background:#4a4032;opacity:0}
.book-strip::after{content:"";position:absolute;bottom:-.7px;left:0;right:0;height:1.4px;background:#b5ad9a;transform:translateZ(.2px)}
.book-turn-shadow{position:absolute;inset:0;pointer-events:none;background:linear-gradient(90deg,transparent,#3b312942,transparent);opacity:0;z-index:3;transform-origin:center}
.book-stage.is-turning .book-bed{pointer-events:none}.book-stage.is-turning{cursor:grabbing}.book-stage.is-turning .book-gutter{opacity:.4}
.memory-volume.is-compact .book-bed{grid-template-columns:1fr}.memory-volume.is-compact .book-gutter{display:none}.memory-volume.is-compact .book-stage{perspective:1300px}
.memory-volume.is-compact .book-leaf{padding:22px;gap:10px;background-image:linear-gradient(90deg,#aba08d18,transparent 6%);border-radius:2px}
.memory-volume.is-compact .book-leaf h2{font-size:1.22rem}.memory-volume.is-compact .book-paragraph{font-size:.88rem;line-height:1.9}.memory-volume.is-compact .book-toolbar{margin-bottom:16px}.memory-volume.is-compact .book-toolbar p{font-size:.77rem}.memory-volume.is-compact .book-photos.is-pair{gap:10px}
.memory-volume.is-compact .book-photo figcaption{font-size:.71rem}.memory-volume.is-compact .book-navigation{gap:10px}.memory-volume.is-compact .book-navigation button{min-width:72px;padding-inline:10px}
.book-empty{padding:64px 24px;text-align:center;color:var(--muted)}
@media(prefers-color-scheme:dark){.memory-volume{--book-paper:#dfdbd2;--book-ink:#26251f;--book-muted:#5e5b52}.book-stage::before{box-shadow:0 20px 30px -12px #0009,0 4px 8px #0006}}
@media(prefers-reduced-motion:reduce){.book-strip{will-change:auto}}
"""

BOOK_JS = r"""
(()=>{
  'use strict';
  const positions=new Map();
  function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text)n.textContent=text;return n;}
  function chunks(text,limit=90){
    const result=[];let rest=(text||'').trim();
    while(rest.length>limit){let at=rest.lastIndexOf(' ',limit);if(at<limit*.55)at=limit;result.push(rest.slice(0,at).trim());rest=rest.slice(at).trim();}
    if(rest)result.push(rest);return result;
  }
  function geometry(progress,width,count){
    const bend=.92*Math.sin(Math.PI*progress),delta=width/count,result=[];let x=0,z=0;
    for(let i=0;i<count;i++){const angle=Math.PI*progress+bend*(2*(i+.5)/count-1);result.push({x,z,angle});x+=Math.cos(angle)*delta;z+=Math.sin(angle)*delta;}
    return result;
  }
  function mount({host,tiles,onOpen,title='',intro='',initialTile=null,reduceMotion=false,compact=false}){
    compact=typeof compact==='object'?Boolean(compact.matches):Boolean(compact);
    reduceMotion=typeof reduceMotion==='object'?Boolean(reduceMotion.matches):Boolean(reduceMotion);
    const cleanups=[];let destroyed=false,frame=0,turn=null,gesture=null,suppressClick=false;
    const source=Array.from(tiles||[]);host.replaceChildren();host.classList.add('memory-volume');host.classList.toggle('is-compact',compact);
    if(!source.length){host.append(node('p','book-empty','이 조건에 해당하는 사진이 없습니다.'));return{destroy(){host.replaceChildren();host.classList.remove('memory-volume','is-compact');}};}
    const key=source.map(t=>t.dataset.preview).join('|');
    const groups=[];let previous=null;
    source.forEach(tile=>{const chapter=tile.closest('.chapter');if(chapter!==previous||!groups.length){groups.push({chapter,tiles:[]});previous=chapter;}groups.at(-1).tiles.push(tile);});
    const pages=[],chapters=[];
    function addPage(type,title,date,text,photos){pages.push({id:pages.length,type,title,date,text,photos});}
    groups.forEach(({chapter,tiles:items})=>{
      const title=chapter?.querySelector('h2')?.textContent.trim()||items[0].dataset.title||'기억의 한 장';
      const date=chapter?.querySelector('.chapter-date')?.textContent.trim()||items[0].dataset.date||'';
      const summary=chapter?.querySelector('.chapter-copy')?.textContent.trim()||'';
      chapters.push({title,date,page:pages.length});
      addPage('photo','',date,'',[items[0]]);
      const headings=chunks(title,42),pageTitle=headings.shift()||title;
      const paragraphs=[...headings,...chunks(summary)];addPage('story',pageTitle,date,paragraphs.shift()||'',items.slice(1,3));
      chunks(paragraphs.join(' '),150).forEach(text=>addPage('text','',date,text,[]));
      for(let i=3;i<items.length;i+=2)addPage('photo','',date,'',items.slice(i,i+2));
      if(pages.length%2)addPage('text',pageTitle,date,'',[]);
    });
    // Endpaper is a deliberate leaf, not a broken/empty image placeholder.
    pages.forEach(p=>{if(p.type==='text'&&!p.text){p.type='endpaper';p.text='사진으로 남은 하루';}});
    const size=compact?1:2;
    let current=Math.min(pages.length-1,positions.get(key)||0);
    const initialPage=initialTile?pages.findIndex(p=>p.photos.includes(initialTile)):-1;if(initialPage>=0)current=initialPage;if(!compact)current-=current%2;
    const heading=node('header','book-heading');heading.append(node('h1','',title||'펼쳐보는 기억의 책'));if(intro)heading.append(node('p','',intro));
    const toolbar=node('div','book-toolbar'),label=node('p','',`${source.length}장의 사진으로 엮은 기억`),tocButton=node('button','','목차');tocButton.type='button';tocButton.setAttribute('aria-expanded','false');toolbar.append(label,tocButton);
    const toc=node('nav','book-toc');toc.hidden=true;toc.setAttribute('aria-label','기억의 책 목차');
    const stage=node('div','book-stage');stage.tabIndex=0;stage.setAttribute('role','group');stage.setAttribute('aria-label','기억의 책. 방향키 또는 드래그로 책장 넘기기');
    const bed=node('div','book-bed'),gutter=node('div','book-gutter'),shadow=node('div','book-turn-shadow');gutter.setAttribute('aria-hidden','true');shadow.setAttribute('aria-hidden','true');stage.append(bed,gutter,shadow);
    const navigation=node('div','book-navigation'),prev=node('button','','‹ 이전'),position=node('span','book-position'),next=node('button','','다음 ›');prev.type=next.type='button';prev.setAttribute('aria-label','이전 책장');next.setAttribute('aria-label','다음 책장');position.setAttribute('aria-live','polite');navigation.append(prev,position,next);
    const hint=node('p','book-hint',compact?'좌우로 넘기고, 사진을 눌러 크게 보세요.':'책장을 끌어 넘기거나 방향키를 사용하세요. 사진은 눌러서 크게 볼 수 있습니다.');
    host.append(heading,toolbar,toc,stage,navigation,hint);
    let width=0,height=0;
    function on(el,event,fn,options){el.addEventListener(event,fn,options);cleanups.push(()=>el.removeEventListener(event,fn,options));}
    function photo(tile){
      const figure=node('figure','book-photo'),button=node('button'),image=node('img');button.type='button';button.dataset.bookPhoto=tile.dataset.preview;button.setAttribute('aria-label',`${tile.dataset.title||'사진'} 크게 보기`);
      image.src=tile.dataset.preview||tile.querySelector('img')?.src||'';image.alt=tile.dataset.alt||tile.dataset.title||'Story 사진';image.decoding='async';image.draggable=false;
      button.append(image);const caption=tile.dataset.title||'';figure.append(button,node('figcaption','',/^추천 사진$|^사진$/.test(caption)?'':caption));
      button.addEventListener('click',()=>{if(!suppressClick&&!turn)onOpen(tile);});
      image.addEventListener('error',()=>{image.hidden=true;button.append(node('span','','사진을 불러오지 못했습니다. 눌러 다시 확인해 주세요.'));},{once:true});
      return figure;
    }
    function leaf(id,side){
      const page=pages[id],el=node('article',`book-leaf is-${side} is-${page?.type||'endpaper'}`);el.dataset.bookPage=String(id);el.setAttribute('aria-label',`${id+1}쪽`);
      if(!page)return el;
      if(page.date&&page.date!==page.title)el.append(node('p','book-page-date',page.date));
      if(page.title)el.append(node('h2','',page.title));
      if(page.text)el.append(node('p','book-paragraph',page.text));
      if(page.photos.length){const photos=node('div',`book-photos${page.photos.length>1?' is-pair':''}`);page.photos.forEach(t=>photos.append(photo(t)));el.append(photos);}
      else{const spacer=node('div');spacer.style.flex='1';el.append(spacer);}
      el.append(node('p','book-folio',String(id+1)));return el;
    }
    function update(){
      positions.set(key,current);if(positions.size>12)positions.delete(positions.keys().next().value);
      position.textContent=`${current+1}${!compact&&current+1<pages.length?` - ${current+2}`:''} / ${pages.length}`;
      prev.disabled=current===0;next.disabled=current+size>=pages.length;
      stage.dataset.bookCurrent=String(current);stage.dataset.bookTotal=String(pages.length);
      for(const b of toc.querySelectorAll('button')){const p=Number(b.dataset.page);b.setAttribute('aria-current',p<=current&&(Number(b.nextElementSibling?.dataset.page)||pages.length)>current?'page':'false');}
    }
    const preloads=new Map();
    function warmNearby(){
      const urls=new Set(pages.slice(Math.max(0,current-size),current+size*3).flatMap(p=>p.photos.map(t=>t.dataset.preview)).filter(Boolean));
      for(const url of preloads.keys())if(!urls.has(url))preloads.delete(url);
      for(const url of urls)if(!preloads.has(url)){const image=new Image();image.decoding='async';image.src=url;preloads.set(url,image);}
    }
    function render(){bed.replaceChildren(leaf(current,compact?'single':'left'));if(!compact)bed.append(leaf(current+1,'right'));update();warmNearby();}
    function measure(){
      const hostStyle=getComputedStyle(host),available=host.clientWidth-parseFloat(hostStyle.paddingLeft||0)-parseFloat(hostStyle.paddingRight||0);
      width=compact?Math.min(available-8,520):Math.min((available-16)/2,580);
      width=Math.max(140,width);height=Math.max(compact?440:420,Math.min(width*1.34,Math.max(compact?440:420,window.innerHeight-stage.getBoundingClientRect().top-125),760));
      stage.style.width=`${width*size}px`;stage.style.height=`${height}px`;
    }
    function clearTurn(){cancelAnimationFrame(frame);if(turn){turn.layer.remove();turn=null;}stage.classList.remove('is-turning');shadow.style.opacity='0';bed.style.visibility='';}
    function pageTexture(page){
      // Capture the measured page locally. A shared bitmap avoids seams from
      // dozens of independently composited HTML/image layers during bending.
      page.style.position='absolute';page.style.visibility='hidden';page.style.left='0';page.style.top='0';stage.append(page);
      try{
        const ratio=Math.min(2,window.devicePixelRatio||1),canvas=document.createElement('canvas');canvas.width=Math.ceil(width*ratio);canvas.height=Math.ceil(height*ratio);
        const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);const box=page.getBoundingClientRect(),style=getComputedStyle(page);
        ctx.fillStyle=style.backgroundColor;ctx.fillRect(0,0,width,height);
        const shade=ctx.createLinearGradient(0,0,width,0);if(page.classList.contains('is-right')){shade.addColorStop(0,'#695f4933');shade.addColorStop(.13,'#695f4900');shade.addColorStop(1,'#695f4900');}else{shade.addColorStop(0,'#73675000');shade.addColorStop(.87,'#73675000');shade.addColorStop(1,'#73675048');}ctx.fillStyle=shade;ctx.fillRect(0,0,width,height);
        for(const image of page.querySelectorAll('img')){
          const bitmap=image.complete&&image.naturalWidth?image:preloads.get(image.src);if(!bitmap?.naturalWidth)continue;
          const r=image.getBoundingClientRect(),fit=Math.min(r.width/bitmap.naturalWidth,r.height/bitmap.naturalHeight),w=bitmap.naturalWidth*fit,h=bitmap.naturalHeight*fit;
          ctx.drawImage(bitmap,r.left-box.left+(r.width-w)/2,r.top-box.top+(r.height-h)/2,w,h);
        }
        for(const element of page.querySelectorAll('h2,.book-page-date,.book-paragraph,figcaption,.book-folio')){
          const text=element.firstChild;if(!text||text.nodeType!==Node.TEXT_NODE)continue;const style=getComputedStyle(element),r=element.getBoundingClientRect();
          ctx.save();ctx.beginPath();ctx.rect(r.left-box.left,r.top-box.top,r.width,r.height);ctx.clip();ctx.fillStyle=style.color;ctx.font=style.font||`${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;ctx.textBaseline='top';
          const range=document.createRange();for(let i=0;i<text.length;i++){range.setStart(text,i);range.setEnd(text,i+1);const letter=range.getBoundingClientRect();ctx.fillText(text.data[i],letter.left-box.left,letter.top-box.top);}ctx.restore();
        }
        return canvas;
      }catch(_){return null;}finally{page.remove();}
    }
    function buildTurn(target){
      if(turn||destroyed||target<0||target>=pages.length)return false;
      if(reduceMotion){current=target;render();return false;}
      const forward=target>current,frontID=compact?(forward?current:target):(forward?current+1:target+1),backID=compact?frontID:(forward?target:current);
      const layer=node('div','book-turn');layer.setAttribute('aria-hidden','true');layer.inert=true;
      const front=leaf(frontID,compact?'single':'right'),back=leaf(backID,compact?'single':'left');
      [front,back].forEach(el=>{el.style.width=`${width}px`;el.style.height=`${height}px`;el.querySelectorAll('img').forEach(i=>{i.loading='eager';});});
      const frontTexture=pageTexture(front),backTexture=pageTexture(back);
      const count=compact?22:32,stripWidth=width/count,strips=[];
      for(let i=0;i<count;i++){
        const strip=node('div','book-strip');strip.style.width=`${stripWidth+1.8}px`;strip.style.height=`${height}px`;strip.style.left=`${compact?0:width}px`;
        const f=node('div','book-surface'),b=node('div','book-surface back'),fc=front.cloneNode(true),bc=back.cloneNode(true),fs=node('div','book-shading'),bs=node('div','book-shading');
        fc.style.left=`${-i*stripWidth}px`;bc.style.left=`${-(width-(i+1)*stripWidth)}px`;
        if(frontTexture&&backTexture){for(const [surface,texture,offset] of [[f,frontTexture,i*stripWidth],[b,backTexture,width-(i+1)*stripWidth]]){
          const canvas=document.createElement('canvas'),ratio=texture.width/width;canvas.width=Math.ceil((stripWidth+1.8)*ratio);canvas.height=texture.height;canvas.style.width='100%';canvas.style.height='100%';canvas.style.display='block';
          canvas.getContext('2d').drawImage(texture,offset*ratio,0,canvas.width,canvas.height,0,0,canvas.width,canvas.height);surface.append(canvas);
        }}else{fc.style.visibility=bc.style.visibility='';f.append(fc);b.append(bc);}
        f.append(fs);b.append(bs);strip.append(f,b);layer.append(strip);strips.push({strip,fs,bs});
      }
      // The next spread sits underneath the flexible sheet throughout its turn.
      bed.replaceChildren(leaf(compact?(forward?target:current):(forward?current:target),'left'));
      if(!compact)bed.append(leaf(forward?target+1:current+1,'right'));
      turn={target,forward,layer,strips,progress:forward?0:1,count};stage.append(layer);stage.classList.add('is-turning');draw(turn.progress);return true;
    }
    function draw(progress){
      if(!turn)return;turn.progress=progress;
      const bend=.92*Math.sin(Math.PI*progress),surface=geometry(progress,width,turn.count);
      turn.strips.forEach(({strip,fs,bs},i)=>{
        const {x,z,angle}=surface[i],degrees=angle*180/Math.PI;
        strip.style.transform=`translate3d(${x}px,0,${z}px) rotateY(${-degrees}deg)`;
        // Lambert-like directional illumination changes with each surface normal.
        const light=.82*Math.cos(angle-.36)+.18;fs.style.opacity=String(Math.min(.3,Math.max(0,(1-light)*.21)));bs.style.opacity=String(Math.min(.27,Math.max(0,(1+light)*.16)));
      });
      shadow.style.opacity=String(Math.sin(Math.PI*progress)*.85);shadow.style.transform=`translateX(${(progress-.5)*width*.55}px) scaleX(${.15+Math.sin(Math.PI*progress)*.72})`;
      stage.dataset.bookBend=String(bend.toFixed(3));
    }
    function settle(commit=true){
      if(!turn)return;const from=turn.progress,to=commit?(turn.forward?1:0):(turn.forward?0:1),start=performance.now(),duration=Math.max(210,Math.abs(to-from)*850);
      const tick=now=>{if(!turn||destroyed)return;const p=Math.min(1,(now-start)/duration),ease=p*p*(3-2*p);draw(from+(to-from)*ease);if(p<1)frame=requestAnimationFrame(tick);else{const target=turn.target;clearTurn();if(commit)current=target;render();}};frame=requestAnimationFrame(tick);
    }
    function go(target){target=Math.max(0,Math.min(pages.length-1,target));if(!compact)target-=target%2;if(target===current||turn)return;if(Math.abs(target-current)!==size||reduceMotion){clearTurn();current=target;render();return;}if(buildTurn(target))settle();}
    chapters.forEach(ch=>{const b=node('button');b.type='button';b.dataset.page=String(ch.page);b.append(node('span','',ch.title),node('span','',`${ch.page+1}쪽`));on(b,'click',()=>{go(ch.page);toc.hidden=true;tocButton.setAttribute('aria-expanded','false');stage.focus({preventScroll:true});});toc.append(b);});
    on(tocButton,'click',()=>{toc.hidden=!toc.hidden;tocButton.setAttribute('aria-expanded',String(!toc.hidden));});
    on(prev,'click',()=>go(current-size));on(next,'click',()=>go(current+size));
    on(stage,'keydown',e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();go(current+(e.key==='ArrowRight'?size:-size));}});
    on(stage,'pointerdown',e=>{if(e.button!==0||turn||e.isPrimary===false)return;gesture={id:e.pointerId,x:e.clientX,y:e.clientY,last:e.clientX,time:performance.now(),started:false};});
    on(stage,'pointermove',e=>{
      if(!gesture||gesture.id!==e.pointerId)return;const dx=e.clientX-gesture.x,dy=e.clientY-gesture.y;
      if(!gesture.started){if(Math.abs(dy)>Math.abs(dx)&&Math.abs(dy)>12){gesture=null;return;}if(Math.abs(dx)<12)return;const target=current+(dx<0?size:-size);if(target<0||target>=pages.length){gesture=null;return;}if(reduceMotion){gesture.started=true;gesture.target=target;}else if(buildTurn(target)){gesture.started=true;stage.setPointerCapture(e.pointerId);}else{gesture=null;return;}}
      if(gesture.started){e.preventDefault();gesture.last=e.clientX;if(turn)draw(Math.max(0,Math.min(1,turn.forward?-dx/(width*1.35):1-dx/(width*1.35))));}
    });
    function release(e,cancelled=false){if(!gesture||gesture.id!==e.pointerId)return;const g=gesture;gesture=null;if(!g.started)return;suppressClick=true;setTimeout(()=>{suppressClick=false;},0);const distance=Math.abs(e.clientX-g.x),speed=distance/Math.max(1,performance.now()-g.time),commit=!cancelled&&(distance>width*.18||speed>.4);if(turn)settle(commit);else if(commit)go(g.target);if(stage.hasPointerCapture(e.pointerId))stage.releasePointerCapture(e.pointerId);}
    on(stage,'pointerup',e=>release(e));on(stage,'pointercancel',e=>release(e,true));
    let resizeFrame=0;const observer=new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{if(destroyed)return;clearTurn();measure();render();});});observer.observe(host);
    measure();render();
    function activeTile(){
      for(let i=current;i<Math.min(pages.length,current+size);i++)if(pages[i].photos.length)return pages[i].photos[0];
      for(let i=current-1;i>=0;i--)if(pages[i].photos.length)return pages[i].photos[0];
      return source[0];
    }
    return{destroy(){destroyed=true;clearTurn();cancelAnimationFrame(resizeFrame);observer.disconnect();cleanups.forEach(f=>f());host.replaceChildren();host.classList.remove('memory-volume','is-compact');},getPage(){return current;},getActiveTile:activeTile,goToPage:go};
  }
  window.PhotosStoryBook={mount,geometry};
})();
"""
