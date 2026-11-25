const fs=require('fs'), path=require('path'), zlib=require('zlib'); const Jimp=require('jimp');
const VERSION=fs.readFileSync(path.join(__dirname,'..','VERSION'),'utf8').trim();
const W=1264,H=1680, CX=W/2;
const BLACK=0x000000ff, WHITE=0xffffffff;

function px(img,x,y,c){ x|=0;y|=0; if(x<0||y<0||x>=W||y>=H)return; img.setPixelColor(c,x,y); }
function disk(img,cx,cy,r,c){ for(let y=-r;y<=r;y++)for(let x=-r;x<=r;x++) if(x*x+y*y<=r*r) px(img,cx+x,cy+y,c); }
function line(img,x1,y1,x2,y2,t,c){ const n=Math.max(Math.abs(x2-x1),Math.abs(y2-y1))|0;
  for(let i=0;i<=n;i++){const x=x1+(x2-x1)*i/n, y=y1+(y2-y1)*i/n; disk(img,x,y,t,c);} }
function arc(img,cx,cy,r,a0,a1,t,c){ for(let a=a0;a<=a1;a+=0.5){const rad=a*Math.PI/180; disk(img,cx+r*Math.cos(rad),cy+r*Math.sin(rad),t,c);} }

function drawIcon(img){
  // WiFi arcs (opening downward) centered above the book, dot at (CX, 560)
  const dotY=580;
  disk(img,CX,dotY,10,BLACK);
  arc(img,CX,dotY,60,205,335,7,BLACK);
  arc(img,CX,dotY,110,210,330,7,BLACK);
  arc(img,CX,dotY,160,215,325,7,BLACK);
  // Book: rounded-ish open book under the wifi
  const bx=CX, by=760, bw=300, bh=200, t=7;
  // two covers as slanted lines meeting at center bottom (open book)
  line(img,bx-bw/2,by-bh/2, bx, by-bh/2+28, t,BLACK); // left top
  line(img,bx+bw/2,by-bh/2, bx, by-bh/2+28, t,BLACK); // right top
  line(img,bx-bw/2,by-bh/2, bx-bw/2,by+bh/2, t,BLACK);// left side
  line(img,bx+bw/2,by-bh/2, bx+bw/2,by+bh/2, t,BLACK);// right side
  line(img,bx-bw/2,by+bh/2, bx, by+bh/2-28, t,BLACK); // left bottom
  line(img,bx+bw/2,by+bh/2, bx, by+bh/2-28, t,BLACK); // right bottom
  line(img,bx,by-bh/2+28, bx,by+bh/2-28, t,BLACK);    // spine
  // page lines
  for(let k=0;k<3;k++){const yy=by-40+k*40;
    line(img,bx-bw/2+40,yy, bx-30,yy,4,BLACK);
    line(img,bx+30,yy, bx+bw/2-40,yy,4,BLACK);}
}

(async()=>{
  const fWord=await Jimp.loadFont(Jimp.FONT_SANS_128_BLACK);
  const fSub =await Jimp.loadFont(Jimp.FONT_SANS_32_BLACK);
  for(const frame of [0,1]){
    const img=new Jimp(W,H,WHITE);
    drawIcon(img);
    img.print(fWord,0,980,{text:"EinsPommes",alignmentX:Jimp.HORIZONTAL_ALIGN_CENTER},W);
    if(frame===1) img.print(fSub,0,1140,{text:`custom firmware · ${VERSION}`,alignmentX:Jimp.HORIZONTAL_ALIGN_CENTER},W);
    // preview
    img.clone().scale(0.35).write(`frames/splash-prev-${frame}.png`);
    // raw 32bpp BGRX + gzip
    const buf=Buffer.alloc(W*H*4);
    for(let i=0;i<W*H;i++){const r=img.bitmap.data[i*4],g=img.bitmap.data[i*4+1],b=img.bitmap.data[i*4+2];
      buf[i*4]=b;buf[i*4+1]=g;buf[i*4+2]=r;buf[i*4+3]=0xff;}
    fs.writeFileSync(`frames/monzaTolino-on-${frame}.raw.gz`, zlib.gzipSync(buf,{level:9}));
    console.log(`frame ${frame}: raw ${buf.length} bytes -> gz ${fs.statSync('frames/monzaTolino-on-'+frame+'.raw.gz').size}`);
  }
})();
