import assert from 'node:assert/strict';
import {addIncidentTimeMachine, demoIncidentHistory, incidentFeatures} from '../static/incident-time-machine.js';

const events = new Map(), frames = new Map();
const slider = {value: '1440', setAttribute() {},
  addEventListener: (name, fn) => events.set(name, fn),
  removeEventListener: name => events.delete(name)};
const output = {textContent: ''};
globalThis.document = {createElement: () => ({style: {}, remove() {},
  querySelector: selector => selector === 'input' ? slider : output})};
let nextFrame = 0;
globalThis.requestAnimationFrame = fn => {frames.set(++nextFrame, fn); return nextFrame;};
globalThis.cancelAnimationFrame = id => frames.delete(id);
const sources = new Map(), layers = new Map(), listeners = new Map(), calls = [];
const map = {
  addSource: (id, value) => sources.set(id, value), getSource: id => sources.get(id),
  removeSource: id => sources.delete(id),
  addLayer: value => layers.set(value.id, value), getLayer: id => layers.get(id),
  removeLayer: id => layers.delete(id),
  setFilter: (id, value) => {calls.push(id); layers.get(id).filter = value;},
  on: (name, fn) => listeners.set(name, fn), off: name => listeners.delete(name),
  fitBounds() {}, jumpTo() {},
  isStyleLoaded: () => false, addControl: control => control.onAdd(),
  removeControl: control => control.onRemove(),
};
const day = '2026-09-29';
const demo = demoIncidentHistory(day);
assert.equal(demo.length, 288);
assert.equal(new Set(demo.map(item=>item.city)).size, 12);
assert.equal(demoIncidentHistory(day)[42].longitude, demo[42].longitude);
const demoStart=Date.parse(`${day}T00:00:00+05:00`);
assert.ok(demo.every(item=>item.data_origin==='synthetic_demo' && Date.parse(item.registered_at)>=demoStart && Date.parse(item.registered_at)<demoStart+86_400_000));
assert.ok(incidentFeatures(demo).features.every(feature=>feature.properties.id.startsWith('DEMO-')));
const start = Date.parse(`${day}T00:00:00+05:00`) / 1000;
const items = [0, 43200, 86399.999, 86400].map((seconds, id) => ({id: String(id),
  registered_at: new Date((start + seconds) * 1000).toISOString(), status: 'pending',
  latitude: 43.2461819, longitude: 76.9269795}));
items.push({...items[1], id: 'missing', latitude: null, longitude: null});
assert.equal(incidentFeatures(items).features.length, 4);
assert.equal(incidentFeatures([{...items[0], latitude: 999}]).features.length, 0);
let visible = [], mapped = 0;
const controller = addIncidentTimeMachine(map, items, day, (next, count) => {visible = next; mapped = count;});
assert.equal(layers.size, 0);
listeners.get('style.load')();
assert.equal(sources.get('time-machine-incidents').data.features.length, 4);
let previous = 0;
for (const minute of [0, 360, 720, 1080, 1440]) {
  slider.value = String(minute);
  events.get('input')(); events.get('input')(); events.get('input')();
  assert.equal(frames.size, 1);
  const before = calls.length;
  const callback = [...frames.values()][0]; frames.clear(); callback();
  assert.equal(calls.length - before, 2);
  const cutoff = start + minute * 60;
  for (const layer of layers.values()) assert.deepEqual(layer.filter, ['all',
    ['>=', ['get', 'timestamp'], start], ['<', ['get', 'timestamp'], start + 86400],
    ['<=', ['get', 'timestamp'], cutoff]]);
  const count = items.filter(f => Date.parse(f.registered_at) / 1000 <= cutoff && Date.parse(f.registered_at) / 1000 < start + 86400).length;
  assert.equal(visible.length, count);
  assert.ok(count >= previous); previous = count;
  assert.ok(output.textContent.endsWith(`обращений: ${count}`));
  if (minute === 0) assert.equal(count, 1);
  if (minute === 1440) {assert.equal(count, 4); assert.equal(mapped, 3);}

}
sources.clear(); layers.clear(); listeners.get('style.load')();
assert.equal(layers.size, 2);
sources.get('time-machine-incidents').setData = data => {sources.get('time-machine-incidents').data = data;};
controller.setItems([], '2026-09-30');
assert.equal(visible.length, 0);
assert.equal(sources.get('time-machine-incidents').data.features.length, 0);
events.get('input')(); controller.remove();
assert.equal(frames.size + events.size + listeners.size + layers.size + sources.size, 0);
console.log('PASS: stored coordinates; missing points excluded; UTC+5 midnight/noon/last millisecond; both filters; frame batching; empty day; style reload; cleanup');
