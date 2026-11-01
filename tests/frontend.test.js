import test from 'node:test';
import assert from 'node:assert/strict';

// ------------------------------------------------------------- tiny DOM mock

class MockElement {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.innerText = '';
    this.innerHTML = '';
    this.className = '';
    this.style = { setProperty(name, value) { this[name] = value; } };
    this.dataset = {};
    this.disabled = false;
    this.hidden = false;
    this.attributes = {};
    this.children = [];
    this.parent = null;
    this.listeners = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    const el = this;
    this.classList = {
      add: (...names) => { el.className = [...new Set([...el.className.split(' ').filter(Boolean), ...names])].join(' '); },
      remove: (...names) => { el.className = el.className.split(' ').filter((n) => n && !names.includes(n)).join(' '); },
      contains: (name) => el.className.split(' ').includes(name),
    };
  }
  get isConnected() { return this.parent !== null; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  append(...nodes) {
    for (const node of nodes) {
      if (typeof node === 'string') { this.innerText += node; } else { this.appendChild(node); }
    }
  }
  replaceChildren(...nodes) { this.children = []; this.innerText = ''; this.append(...nodes); }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  replaceWith(node) {
    const parent = this.parent;
    const index = parent.children.indexOf(this);
    node.parent = parent;
    parent.children[index] = node;
    this.parent = null;
  }
  addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
  click() { for (const callback of this.listeners.click ?? []) callback({ preventDefault() {} }); }
  focus() {}
  querySelector(selector) {
    const cls = selector.replace(/^\./, '');
    for (const child of this.children) {
      if (child.className.split(' ').includes(cls)) return child;
      const found = child.querySelector(selector);
      if (found) return found;
    }
    return null;
  }
  /** All text below this node (for assertions). */
  get text() { return [this.innerText, ...this.children.map((c) => c.text)].join(' ').trim(); }
}

let elements = {};

function installDom() {
  const svc = (id) => {
    const item = new MockElement();
    const value = new MockElement('span');
    value.className = 'svc-value';
    item.appendChild(value);
    return item;
  };
  elements = {
    'record-btn': new MockElement('button'),
    core: new MockElement(),
    'state-title': new MockElement(),
    display: new MockElement(),
    activity: new MockElement(),
    status: new MockElement(),
    visualizer: new MockElement(),
    'session-label': new MockElement(),
    'agent-label': new MockElement(),
    metrics: new MockElement(),
    'svc-stt': svc(), 'svc-tts': svc(), 'svc-agent': svc(), 'svc-link': svc(),
  };

  globalThis.HTMLElement = MockElement;
  globalThis.document = {
    body: new MockElement('body'),
    getElementById: (id) => elements[id] ?? null,
    createElement: (tag) => new MockElement(tag),
  };

  const audioNode = () => ({ connect(next) { return next ?? this; } });
  globalThis.window = {
    location: { protocol: 'http:', host: 'localhost:8080' },
    AudioContext: class {
      constructor() { this.state = 'running'; this.currentTime = 0; this.destination = audioNode(); }
      async resume() { this.state = 'running'; }
      createMediaStreamSource() { return audioNode(); }
      createMediaElementSource() { return audioNode(); }
      createAnalyser() { return { ...audioNode(), context: this, fftSize: 0, frequencyBinCount: 8, getByteFrequencyData() {} }; }
      // Mock WAV decode: a chunk of N bytes lasts N seconds, to check scheduling.
      async decodeAudioData(data) { return { duration: data.byteLength }; }
      createBufferSource() {
        const source = {
          ...audioNode(), buffer: null, startAt: null, stopped: false, onended: null,
          start(at) { this.startAt = at; },
          stop() { this.stopped = true; },
          finish() { this.onended?.(); },
        };
        globalThis.__sources.push(source);
        return source;
      }
      createOscillator() {
        const param = { setValueAtTime() {}, exponentialRampToValueAtTime() {} };
        return { ...audioNode(), type: '', frequency: param, start() {}, stop() {} };
      }
      createGain() {
        return { ...audioNode(), gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} } };
      }
      async close() { this.state = 'closed'; }
    },
    webkitAudioContext: undefined,
  };

  globalThis.navigator = {
    mediaDevices: { async getUserMedia() { return { getTracks: () => [] }; } },
  };

  globalThis.MediaRecorder = class {
    static isTypeSupported() { return true; }
    constructor(stream, options) { this.stream = stream; this.mimeType = options.mimeType; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.onstop?.(); }
  };

  // Never run frames synchronously: meters loop until playback stops.
  globalThis.requestAnimationFrame = () => 1;
  globalThis.cancelAnimationFrame = () => {};

  globalThis.Audio = class {
    constructor(src) {
      this.src = src;
      this.paused = false;
      this.ended = false;
      this.listeners = {};
      globalThis.__lastAudio = this;
    }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    dispatchEvent(event) { this.listeners[event.type]?.(event); }
    async play() {}
    pause() { this.paused = true; }
  };

  globalThis.WebSocket = class {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 1; this.sent = []; }
    send(payload) { this.sent.push(payload); }
    close() { this.readyState = 3; }
  };
}

installDom();

const { state } = await import('../static/js/state.js');
const uiModule = await import('../static/js/ui.js');
const { startRecording } = await import('../static/js/recorder.js');
const ws = await import('../static/js/websocket.js');
const { COLORS } = await import('../static/js/constants.js');
const { stopPlayback } = await import('../static/js/audio.js');

const { ui, setStatus, updateRecordButton, effectiveState } = uiModule;
const { handleServerMessage, handleAudioFrame, parseServerMessage, consumeTokenFromUrl, interrupt } = ws;

function reset() {
  installDom();
  Object.assign(ui, {
    recordBtn: null, core: null, title: null, display: null, status: null,
    visualizer: null, streamingReply: null, renderedState: null,
  });
  Object.assign(state, {
    socket: null, connected: true, serverState: 'idle', muted: false,
    isRecording: false, isStarting: false, isStopping: false, isProcessing: false,
    stopRequested: false, recordingRequested: false, discardRecording: false,
    awaitingAudio: false, analyser: null, meterId: null,
  });
  stopPlayback();
  state.playing = false;
  globalThis.__sources = [];
}

const $ = (id) => elements[id];
const messages = () => $('display').children;
const cards = () => $('activity').children;

// ------------------------------------------------------------- core + status

test('the core shows the effective state with its title and hint', () => {
  reset();
  updateRecordButton();
  assert.equal($('core').className, 'core is-idle');
  assert.equal($('state-title').innerText, 'Standing by');

  state.isRecording = true;
  updateRecordButton();
  assert.equal($('core').className, 'core is-listening');
  assert.equal($('record-btn').getAttribute('aria-label'), 'Stop recording and send');
  assert.equal($('record-btn').getAttribute('aria-pressed'), 'true');

  state.isRecording = false;
  state.connected = false;
  updateRecordButton();
  assert.equal($('core').className, 'core is-offline');
  assert.equal($('record-btn').disabled, true);
});

test('local activity outranks the server state', () => {
  reset();
  state.serverState = 'thinking';
  assert.equal(effectiveState(), 'thinking');
  state.awaitingAudio = true;
  assert.equal(effectiveState(), 'speaking');
  state.isRecording = true;
  assert.equal(effectiveState(), 'listening');
});

test('setStatus writes the hint line', () => {
  reset();
  setStatus('Hello', COLORS.amber);
  assert.equal($('status').innerText, 'Hello');
  assert.equal($('status').style.color, COLORS.amber);
});

test('startRecording blocks when the websocket is not connected', async () => {
  reset();
  await startRecording();
  assert.equal($('status').innerText, 'WebSocket not connected');
  assert.equal($('status').style.color, COLORS.error);
  assert.equal(state.recordingRequested, false);
});

test('state events drive the core and the processing flag', async () => {
  reset();
  await handleServerMessage({ type: 'state', value: 'thinking' });
  assert.equal(state.isProcessing, true);
  assert.equal($('core').className, 'core is-thinking');
  assert.equal($('state-title').innerText, 'Thinking');

  await handleServerMessage({ type: 'state', value: 'idle' });
  assert.equal(state.isProcessing, false);
  assert.equal($('state-title').innerText, 'Standing by');
});

// ------------------------------------------------------------- protocol

test('parseServerMessage rejects invalid payloads', () => {
  const quiet = console.error;
  console.error = () => {};
  try {
    assert.equal(parseServerMessage('not json'), null);
    assert.equal(parseServerMessage('{"text":"no type"}'), null);
    assert.deepEqual(parseServerMessage('{"type":"state","value":"idle"}'), { type: 'state', value: 'idle' });
  } finally {
    console.error = quiet;
  }
});

test('session wakes the HUD and labels the link and agent', async () => {
  reset();
  state.connected = false;
  document.body.classList.add('booting');
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

  await handleServerMessage({ type: 'session', session_id: 'abc12345', protocol: 1, agent: 'echo' });

  assert.equal(state.sessionId, 'abc12345');
  assert.equal(state.connected, true);
  assert.equal(document.body.classList.contains('booting'), false);
  assert.equal($('svc-link').dataset.status, 'ready');
  assert.match($('session-label').innerText, /abc1·2345/);
  assert.equal($('agent-label').text, 'Echo · placeholder');
});

// ------------------------------------------------------------- conversation

test('agent tokens stream into one reply, finalized by agent.done', async () => {
  reset();
  await handleServerMessage({ type: 'transcript.final', text: 'hi' });
  await handleServerMessage({ type: 'agent.token', text: 'I heard' });
  await handleServerMessage({ type: 'agent.token', text: ' you' });

  assert.equal(messages().length, 2);
  assert.equal(messages()[0].className, 'msg msg-user');
  assert.match(messages()[0].text, /You .* hi/);
  const reply = messages()[1];
  assert.ok(reply.classList.contains('msg-streaming'));
  assert.match(reply.text, /I heard you$/);

  await handleServerMessage({ type: 'agent.done', text: 'I heard you say: hi' });
  assert.match(reply.text, /I heard you say: hi$/);
  assert.equal(reply.classList.contains('msg-streaming'), false);
  assert.equal(ui.streamingReply, null);
});

test('interrupt marks the partial reply and sends interrupt', async () => {
  reset();
  state.socket = new WebSocket('ws://x');
  await handleServerMessage({ type: 'agent.token', text: 'Half a' });

  interrupt();

  assert.ok(messages()[0].classList.contains('msg-interrupted'));
  assert.deepEqual(JSON.parse(state.socket.sent.at(-1)), { type: 'interrupt' });
});

// ------------------------------------------------------------- audio

const chunk = (seq, bytes) => ({ type: 'tts.chunk', seq, final: false, format: 'wav', bytes });
const wav = (bytes) => new Uint8Array(bytes).buffer;

test('tts.chunk waits for the binary WAV frame, then plays it', async () => {
  reset();
  await handleServerMessage(chunk(0, 2));
  assert.equal(state.awaitingAudio, true);
  assert.equal($('core').className, 'core is-speaking');
  assert.equal(globalThis.__sources.length, 0, 'must not play before the frame arrives');

  await handleAudioFrame(wav(2));

  assert.equal(state.awaitingAudio, false);
  assert.equal(globalThis.__sources.length, 1);
  assert.equal(state.playing, true);

  globalThis.__sources[0].finish();
  assert.equal(state.playing, false);
  assert.equal($('core').className, 'core is-idle');
});

test('sentence chunks are scheduled back to back, without gaps', async () => {
  reset();
  for (const [seq, seconds] of [[0, 2], [1, 3], [2, 1]]) {
    await handleServerMessage(chunk(seq, seconds));
    await handleAudioFrame(wav(seconds));
  }
  const [first, second, third] = globalThis.__sources;
  assert.ok(Math.abs(second.startAt - (first.startAt + 2)) < 1e-9, 'second starts when first ends');
  assert.ok(Math.abs(third.startAt - (second.startAt + 3)) < 1e-9, 'third starts when second ends');

  first.finish();
  assert.equal(state.playing, true, 'still speaking while chunks remain');
  second.finish();
  third.finish();
  assert.equal(state.playing, false);
});

test('interrupt flushes every queued chunk', async () => {
  reset();
  state.socket = new WebSocket('ws://x');
  for (const seq of [0, 1]) {
    await handleServerMessage(chunk(seq, 1));
    await handleAudioFrame(wav(1));
  }

  interrupt();

  assert.ok(globalThis.__sources.every((source) => source.stopped));
  assert.equal(state.playing, false);
  assert.equal($('core').className, 'core is-idle');

  // The next reply starts right away, not after the flushed audio would have ended.
  await handleServerMessage(chunk(0, 1));
  await handleAudioFrame(wav(1));
  assert.ok(globalThis.__sources.at(-1).startAt < 1);
});

test('muted: reply audio is not played', async () => {
  reset();
  state.muted = true;
  await handleServerMessage(chunk(0, 1));
  await handleAudioFrame(wav(1));
  assert.equal(globalThis.__sources.length, 0);
  assert.equal(state.awaitingAudio, false);
});

test('reply audio is dropped if the user is already recording again', async () => {
  reset();
  state.awaitingAudio = true;
  state.isRecording = true;
  await handleAudioFrame(wav(1));
  assert.equal(globalThis.__sources.length, 0);
  assert.equal(state.awaitingAudio, false);
});

test('unexpected binary frames are ignored', async () => {
  reset();
  const quiet = console.warn;
  console.warn = () => {};
  await handleAudioFrame(wav(1));
  console.warn = quiet;
  assert.equal(globalThis.__sources.length, 0);
});

// ------------------------------------------------------------- activity

test('tool events render one card that updates in place', async () => {
  reset();
  await handleServerMessage({ type: 'agent.tool_start', id: 't1', name: 'web.search', summary: 'Searching' });
  assert.equal(cards().length, 1);
  assert.equal(cards()[0].className, 'tool tool-running');
  assert.match(cards()[0].text, /Searching web\.search · running/);

  await handleServerMessage({ type: 'agent.tool_end', id: 't1', ok: true, summary: 'Found 3 results' });
  assert.equal(cards().length, 1);
  assert.equal(cards()[0].className, 'tool tool-ok');
  assert.match(cards()[0].text, /Found 3 results web\.search · \d+\.\d s/);
});

test('approval card sends the decision once and settles', async () => {
  reset();
  state.socket = new WebSocket('ws://x');
  await handleServerMessage({ type: 'agent.approval_request', id: 'a1', action: 'Restart Plex', detail: 'docker restart plex' });

  const card = cards()[0];
  assert.equal(card.className, 'approval');
  const actions = card.querySelector('approval-actions');
  const [deny, approve] = actions.children;
  assert.equal(approve.innerText, 'Approve');

  approve.click();
  approve.click(); // second click must be a no-op

  const responses = state.socket.sent.map((s) => JSON.parse(s)).filter((m) => m.type === 'approval.response');
  assert.deepEqual(responses, [{ type: 'approval.response', id: 'a1', approved: true }]);
  assert.ok(card.classList.contains('approval-approved'));
  assert.equal(card.querySelector('approval-actions'), null, 'buttons replaced by the result');
  assert.match(card.querySelector('approval-result').innerText, /Approved/);
  void deny;
});

test('errors clear pending audio, cut off tools and show the message', async () => {
  reset();
  state.awaitingAudio = true;
  await handleServerMessage({ type: 'agent.tool_start', id: 't9', name: 'shell', summary: 'Running' });
  const quiet = console.error;
  console.error = () => {};
  globalThis.fetch = async () => { throw new Error('offline'); };
  await handleServerMessage({ type: 'error', code: 'tts_unavailable', message: 'Speech service is offline' });
  console.error = quiet;

  assert.equal(state.awaitingAudio, false);
  assert.equal($('status').innerText, 'Speech service is offline');
  assert.equal($('status').style.color, COLORS.error);
  assert.equal(cards()[0].className, 'tool tool-failed');
});

test('metrics appear in the status strip', async () => {
  reset();
  await handleServerMessage({ type: 'metrics', stt_ms: 130, first_token_ms: 2, first_audio_ms: 876, total_ms: 912 });
  assert.equal($('metrics').innerText, 'last turn · STT 130 ms · first audio 876 ms · total 912 ms');
});

// ------------------------------------------------------------- pairing

test('#token= in the URL is saved and removed from the address bar', () => {
  installDom();
  const saved = new Map();
  globalThis.localStorage = {
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, value),
    removeItem: (key) => saved.delete(key),
  };
  let replacedWith = null;
  globalThis.history = { replaceState: (_s, _t, url) => { replacedWith = url; } };
  globalThis.window.location = { ...globalThis.window.location, hash: '#token=abc%2F123', pathname: '/', search: '' };

  consumeTokenFromUrl();

  assert.equal(saved.get('artemis.access_token'), 'abc/123');
  assert.equal(replacedWith, '/');
});

test('URLs without a token are left alone', () => {
  installDom();
  let replaced = false;
  globalThis.history = { replaceState: () => { replaced = true; } };
  globalThis.window.location = { ...globalThis.window.location, hash: '', pathname: '/', search: '' };

  consumeTokenFromUrl();

  assert.equal(replaced, false);
});

// ------------------------------------------------------------- Phase 7: hands-free

const { onPlaybackIdle } = await import('../static/js/audio.js');
const { notifyPlaybackIdle } = await import('../static/js/handsfree.js');
onPlaybackIdle(notifyPlaybackIdle); // as main.js wires it

const sentTypes = () => state.socket.sent.map((s) => JSON.parse(s).type);

function handsFreeReset() {
  reset();
  state.socket = new WebSocket('ws://x');
  state.handsFree = 'armed';
  state.suppressFollowUp = false;
}

test('hands-free status drives the core and the hint', async () => {
  handsFreeReset();
  await handleServerMessage({ type: 'handsfree', status: 'armed', reason: 'started' });
  assert.equal($('core').className, 'core is-idle');
  assert.match($('status').innerText, /Artemis/);

  await handleServerMessage({ type: 'handsfree', status: 'listening', reason: 'follow_up' });
  assert.equal($('core').className, 'core is-listening');
  assert.match($('status').innerText, /listening/);
});

test('a reply that finishes playing opens the follow-up window', async () => {
  handsFreeReset();
  await handleServerMessage({ type: 'state', value: 'speaking' });
  await handleServerMessage(chunk(0, 1));
  await handleAudioFrame(wav(1));
  await handleServerMessage({ type: 'state', value: 'idle' });
  assert.ok(!sentTypes().includes('playback.idle'), 'not while audio is still playing');

  globalThis.__sources[0].finish();
  assert.deepEqual(sentTypes().filter((t) => t === 'playback.idle'), ['playback.idle']);
});

test('a text-only reply (muted) opens the follow-up window when the turn ends', async () => {
  handsFreeReset();
  state.muted = true;
  await handleServerMessage({ type: 'state', value: 'thinking' });
  await handleServerMessage({ type: 'state', value: 'idle' });
  assert.ok(sentTypes().includes('playback.idle'));
});

test('an interrupted reply does not open the follow-up window', async () => {
  handsFreeReset();
  await handleServerMessage({ type: 'state', value: 'speaking' });
  await handleServerMessage(chunk(0, 1));
  await handleAudioFrame(wav(1));
  await handleServerMessage({ type: 'notice', code: 'interrupted', message: 'Interrupted' });
  await handleServerMessage({ type: 'state', value: 'idle' });

  assert.ok(globalThis.__sources[0].stopped, 'voice "Artemis, stop" silences playback');
  assert.ok(!sentTypes().includes('playback.idle'));
});

test('without hands-free, nothing extra is sent', async () => {
  handsFreeReset();
  state.handsFree = 'off';
  await handleServerMessage({ type: 'state', value: 'thinking' });
  await handleServerMessage({ type: 'state', value: 'idle' });
  assert.deepEqual(sentTypes(), []);
});

test('an approval answered by voice settles its card without a second answer', async () => {
  handsFreeReset();
  await handleServerMessage({ type: 'agent.approval_request', id: 'a9', action: 'Restart Plex', detail: 'systemctl restart plex' });
  const card = cards()[0];
  assert.match(card.text, /say .approve/, 'card mentions voice answers');

  await handleServerMessage({ type: 'approval.resolved', id: 'a9', approved: false });

  assert.ok(card.classList.contains('approval-denied'));
  assert.match(card.querySelector('approval-result').innerText, /Denied by voice/);
  assert.ok(!sentTypes().includes('approval.response'), 'the server already has the answer');
});
