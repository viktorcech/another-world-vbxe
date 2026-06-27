// Faithful VM oracle: runs another.js's EXACT VM logic on jail bytecode (no render),
// to see how far a CORRECT interpreter progresses jail vs our game_sim.
const fs = require('fs');
const DIR = __dirname + '/../out/jaildata/';
let bytecode  = new Uint8Array(fs.readFileSync(DIR + 'bytecode.bin'));
let polygons1 = new Uint8Array(fs.readFileSync(DIR + 'poly1.bin'));
let polygons2 = new Uint8Array(fs.readFileSync(DIR + 'poly2.bin'));

const VAR_HERO_POS_UP_DOWN=0xe5, VAR_HERO_ACTION=0xfa, VAR_HERO_POS_JUMP_DOWN=0xfb,
      VAR_HERO_POS_LEFT_RIGHT=0xfc, VAR_HERO_POS_MASK=0xfd, VAR_HERO_ACTION_POS_MASK=0xfe,
      VAR_PAUSE_SLICES=0xff, VAR_SCREEN=0x67;

let vars = new Array(256).fill(0);
let tasks = new Array(64);
let bytecode_offset, task_num, task_paused, next_part=0, next_palette=-1, delay=0;

function read_byte(){ const v=bytecode[bytecode_offset]; bytecode_offset+=1; return v; }
function read_word(){ const v=(bytecode[bytecode_offset]<<8)|bytecode[bytecode_offset+1]; bytecode_offset+=2; return v; }
function to_signed(value,bits){ const mask=1<<(bits-1); return value-((value&mask)<<1); }

// ---- rendering stubbed (does not affect VM branching) ----
function select_page(){} function fill_page(){} function copy_page(){}
function draw_string(){} function draw_shape(){} function update_display(){}

const opcodes = {
 0x00:function(){const n=read_byte();const i=to_signed(read_word(),16);vars[n]=i;},
 0x01:function(){const d=read_byte();const s=read_byte();vars[d]=vars[s];},
 0x02:function(){const d=read_byte();const s=read_byte();vars[d]+=vars[s];},
 0x03:function(){const n=read_byte();const i=to_signed(read_word(),16);vars[n]+=i;},
 0x04:function(){const a=read_word();tasks[task_num].stack.push(bytecode_offset);bytecode_offset=a;},
 0x05:function(){bytecode_offset=tasks[task_num].stack.pop();},
 0x06:function(){task_paused=true;},
 0x07:function(){bytecode_offset=read_word();},
 0x08:function(){const n=read_byte();const a=read_word();tasks[n].next_offset=a;},
 0x09:function(){const n=read_byte();vars[n]-=1;const a=read_word();if(vars[n]!=0)bytecode_offset=a;},
 0x0a:function(){const op=read_byte();const b=vars[read_byte()];let a;
   if(op&0x80)a=vars[read_byte()];else if(op&0x40)a=to_signed(read_word(),16);else a=read_byte();
   const ad=read_word();switch(op&7){case 0:if(b==a)bytecode_offset=ad;break;case 1:if(b!=a)bytecode_offset=ad;break;
   case 2:if(b>a)bytecode_offset=ad;break;case 3:if(b>=a)bytecode_offset=ad;break;case 4:if(b<a)bytecode_offset=ad;break;
   case 5:if(b<=a)bytecode_offset=ad;break;}},
 0x0b:function(){next_palette=read_word()>>8;},
 0x0c:function(){const s=read_byte();const e=read_byte();const st=read_byte();
   if(st==2){for(let i=s;i<=e;++i)tasks[i].next_offset=-2;}else{for(let i=s;i<=e;++i)tasks[i].next_state=st;}},
 0x0d:function(){select_page(read_byte());},
 0x0e:function(){const n=read_byte();const c=read_byte();fill_page(n,c);},
 0x0f:function(){const s=read_byte();const d=read_byte();copy_page(s,d);},
 0x10:function(){const n=read_byte();delay+=vars[VAR_PAUSE_SLICES]*1000/50;vars[0xf7]=0;update_display(n);},
 0x11:function(){bytecode_offset=-1;task_paused=true;},
 0x12:function(){const n=read_word();const x=read_byte();const y=read_byte();const c=read_byte();draw_string(n,c,x,y);},
 0x13:function(){const d=read_byte();const s=read_byte();vars[d]-=vars[s];},
 0x14:function(){const n=read_byte();const i=read_word();vars[n]=to_signed((vars[n]&i)&0xffff,16);},
 0x15:function(){const n=read_byte();const i=read_word();vars[n]=to_signed((vars[n]|i)&0xffff,16);},
 0x16:function(){const n=read_byte();const i=read_word()&15;vars[n]=to_signed((vars[n]<<i)&0xffff,16);},
 0x17:function(){const n=read_byte();const i=read_word()&15;vars[n]=to_signed((vars[n]&0xffff)>>i,16);},
 0x18:function(){read_word();read_byte();read_byte();read_byte();},
 0x19:function(){const n=read_word();if(n>16000)next_part=n;},
 0x1a:function(){read_word();read_word();read_byte();}
};

function execute_task(){
  while(!task_paused){
    const opcode=read_byte();
    if(opcode&0x80){ read_byte(); read_byte(); read_byte(); /* draw_bg: off,x,y from bytecode */ }
    else if(opcode&0x40){
      read_word(); let x=read_byte();
      if((opcode&0x20)==0){ if((opcode&0x10)==0) read_byte(); }
      else { }
      read_byte(); // y
      if((opcode&8)==0){ if((opcode&4)==0) read_byte(); }
      if((opcode&2)==0){ if(opcode&1) read_byte(); } else { if(opcode&1){} else read_byte(); }
    } else { opcodes[opcode](); }
  }
}

function update_input(){ // no keys pressed
  vars[VAR_HERO_POS_LEFT_RIGHT]=0; vars[VAR_HERO_POS_JUMP_DOWN]=0; vars[VAR_HERO_POS_UP_DOWN]=0;
  vars[VAR_HERO_POS_MASK]=0; vars[VAR_HERO_ACTION]=0; vars[VAR_HERO_ACTION_POS_MASK]=0;
}

function run_tasks(){
  if(next_part!=0){ /* would restart; jail only */ next_part=0; }
  for(let i=0;i<tasks.length;++i){
    tasks[i].state=tasks[i].next_state;
    const off=tasks[i].next_offset;
    if(off!=-1){ tasks[i].offset=(off==-2)?-1:off; tasks[i].next_offset=-1; }
  }
  update_input();
  for(let i=0;i<tasks.length;++i){
    if(tasks[i].state==0){
      const off=tasks[i].offset;
      if(off!=-1){ bytecode_offset=off; tasks[i].stack.length=0; task_num=i; task_paused=false; execute_task(); tasks[i].offset=bytecode_offset; }
    }
  }
}

function reset(v0){
  for(let i=0;i<64;++i) tasks[i]={state:0,next_state:0,offset:-1,next_offset:-1,stack:[]};
  tasks[0].offset=0;
  vars.fill(0);
  vars[0xbc]=0x10; vars[0xc6]=0x80; vars[0xf2]=6000; vars[0xdc]=33; vars[0xe4]=20;
  vars[0]=v0;
}

for(const v0 of [0,21]){
  reset(v0);
  let seq=[], last=null;
  for(let t=0;t<3000;++t){
    run_tasks();
    const sc=vars[VAR_SCREEN]&0xffff;
    if(sc!==last){ seq.push(sc); last=sc; }
  }
  console.log(`another.js VM, jail V0=${v0}: screen-timeline=`+JSON.stringify(seq.slice(0,40)));
}
