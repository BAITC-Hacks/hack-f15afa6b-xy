import fs from 'node:fs/promises';
import assert from 'node:assert/strict';

const source=await fs.readFile(new URL('../static/voice.js',import.meta.url),'utf8');
const voice=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const quiet=new Float32Array(4800).fill(.0001);
assert.throws(()=>voice.prepareSpeech(quiet,48000),/слишком тихо/);
const speech=Float32Array.from({length:4800},(_,i)=>.02*Math.sin(i/8));
const prepared=voice.prepareSpeech(speech,48000);
assert.equal(prepared.length,1600);
assert.ok(Math.max(...prepared)>.05);
const loud=Float32Array.from({length:1600},(_,i)=>.99*Math.sin(i/8));
assert.ok(Math.max(...voice.prepareSpeech(loud,16000))<=.93);
console.log('PASS: quiet audio is rejected and audible speech is normalized to 16 kHz');
