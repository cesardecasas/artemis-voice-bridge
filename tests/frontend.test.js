import test from 'node:test';
import assert from 'node:assert/strict';

function makeMockElement() {
  return {
    innerText: '',
    textContent: '',
    style: {},
    disabled: false,
    attributes: {},
    children: [],
    classList: {
      toggle() {},
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    getAttribute(name) {
      return this.attributes[name];
    },
    appendChild(child) {
      this.children.push(child);
    },
    getElementsByClassName() {
      return [];
    },
    addEventListener() {},
    scrollTop: 0,
    scrollHeight: 0,
    innerHTML: '',
  };
}

function installDom() {
  const recordBtn = makeMockElement();
  const display = makeMockElement();
  const status = makeMockElement();
  const visualizer = makeMockElement();

  const elements = {
    'record-btn': recordBtn,
    'display': display,
    'status': status,
    'visualizer': visualizer,
  };

  globalThis.document = {
    getElementById(id) {
      return elements[id];
    },
    createElement() {
      return makeMockElement();
    },
  };

  globalThis.window = {
    location: {
      protocol: 'http:',
      host: 'localhost:8080',
    },
    AudioContext: class {
      constructor() {
        this.state = 'running';
      }
      createMediaStreamSource() {
        return { connect() {} };
      }
      createAnalyser() {
        return {
          fftSize: 0,
          frequencyBinCount: 8,
          getByteFrequencyData() {},
        };
      }
      close() {
        this.state = 'closed';
      }
    },
    webkitAudioContext: undefined,
  };

  globalThis.navigator = {
    mediaDevices: {
      async getUserMedia() {
        return {
          getTracks() {
            return [];
          },
        };
      },
    },
  };

  globalThis.MediaRecorder = class {
    static isTypeSupported() {
      return true;
    }

    constructor(stream, options) {
      this.stream = stream;
      this.mimeType = options.mimeType;
      this.state = 'inactive';
      this.ondataavailable = null;
      this.onstop = null;
      this.onerror = null;
    }

    start() {
      this.state = 'recording';
    }

    stop() {
      this.state = 'inactive';
      if (this.onstop) {
        this.onstop();
      }
    }
  };

  globalThis.requestAnimationFrame = (callback) => {
    callback();
    return 1;
  };

  globalThis.cancelAnimationFrame = () => {};
  globalThis.Audio = class {
    constructor(src) {
      this.src = src;
      this.paused = false;
      this.ended = false;
      this.listeners = {};
      globalThis.__lastAudio = this;
    }
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    }
    async play() {}
    pause() {
      this.paused = true;
    }
  };

  globalThis.WebSocket = class {
    static OPEN = 1;

    constructor(url) {
      this.url = url;
      this.readyState = WebSocket.OPEN;
      this.binaryType = 'arraybuffer';
      this.onopen = null;
      this.onmessage = null;
      this.onclose = null;
      this.sent = [];
    }

    send(payload) {
      this.sent.push(payload);
    }

    close() {
      this.readyState = 3;
    }
  };
}

installDom();

const stateModule = await import('../static/js/state.js');
const uiModule = await import('../static/js/ui.js');
const recorderModule = await import('../static/js/recorder.js');
const websocketModule = await import('../static/js/websocket.js');

const { state } = stateModule;
const { ui, setStatus, updateRecordButton } = uiModule;
const { startRecording } = recorderModule;
const { handleServerMessage, handleAudioFrame, parseServerMessage } = websocketModule;

function resetUiRefs() {
  ui.recordBtn = document.getElementById('record-btn');
  ui.display = document.getElementById('display');
  ui.status = document.getElementById('status');
  ui.visualizer = document.getElementById('visualizer');
}

test('ui helpers update the status and record button state', () => {
  installDom();
  resetUiRefs();

  state.isRecording = true;
  state.isStarting = false;
  state.isProcessing = false;
  state.isStopping = false;

  setStatus('Listening...', '#00ff00');
  updateRecordButton();

  assert.equal(document.getElementById('status').innerText, 'Listening...');
  assert.equal(document.getElementById('record-btn').getAttribute('aria-label'), 'Stop recording');
  assert.equal(document.getElementById('record-btn').getAttribute('aria-pressed'), 'true');
});

test('startRecording blocks when websocket is not connected', async () => {
  installDom();
  resetUiRefs();

  state.socket = null;
  state.isRecording = false;
  state.isStarting = false;
  state.isStopping = false;
  state.isProcessing = false;

  await startRecording();

  assert.equal(document.getElementById('status').innerText, 'WebSocket not connected');
  assert.equal(document.getElementById('status').style.color, '#ff5c7a'); // COLORS.error
  assert.equal(state.recordingRequested, false);
});

test('state tracks the recording lifecycle', () => {
  state.socket = null;
  state.isRecording = false;
  state.isStarting = false;
  state.isStopping = false;
  state.isProcessing = false;
  state.stopRequested = false;
  state.recordingRequested = false;
  state.recordedBytes = 0;

  state.isRecording = true;
  state.recordedBytes = 128;

  assert.equal(state.isRecording, true);
  assert.equal(state.recordedBytes, 128);
  assert.equal(state.recordingRequested, false);
});

function resetState() {
  state.socket = null;
  state.serverState = 'idle';
  state.isRecording = false;
  state.isStarting = false;
  state.isStopping = false;
  state.isProcessing = false;
  state.awaitingAudio = false;
  state.currentAudio = null;
  ui.streamingReply = null;
  ui.tools.clear();
  globalThis.__lastAudio = undefined;
}

function lastMessage() {
  return document.getElementById('display').children.at(-1);
}

test('parseServerMessage rejects invalid payloads', () => {
  assert.equal(parseServerMessage('not json'), null);
  assert.equal(parseServerMessage('{"text":"no type"}'), null);
  assert.deepEqual(parseServerMessage('{"type":"state","value":"idle"}'), { type: 'state', value: 'idle' });
});

test('state events drive status and processing flag', async () => {
  installDom();
  resetUiRefs();
  resetState();

  await handleServerMessage({ type: 'state', value: 'thinking' });
  assert.equal(state.isProcessing, true);
  assert.equal(document.getElementById('status').innerText, 'Thinking...');

  await handleServerMessage({ type: 'state', value: 'idle' });
  assert.equal(state.isProcessing, false);
  assert.equal(document.getElementById('status').innerText, 'Connected');
});

test('agent tokens stream into one reply bubble, finalized by agent.done', async () => {
  installDom();
  resetUiRefs();
  resetState();

  await handleServerMessage({ type: 'transcript.final', text: 'hi' });
  await handleServerMessage({ type: 'agent.token', text: 'I heard' });
  await handleServerMessage({ type: 'agent.token', text: ' you' });

  const display = document.getElementById('display');
  assert.equal(display.children.length, 2);
  assert.equal(display.children[0].innerText, 'You: hi');
  assert.equal(lastMessage().innerText, 'Artemis: I heard you');

  await handleServerMessage({ type: 'agent.done', text: 'I heard you say: hi' });
  assert.equal(lastMessage().innerText, 'Artemis: I heard you say: hi');
  assert.equal(ui.streamingReply, null);
});

test('tts.chunk waits for the binary WAV frame, then plays it', async () => {
  installDom();
  resetUiRefs();
  resetState();

  await handleServerMessage({ type: 'tts.chunk', seq: 0, final: true, format: 'wav', bytes: 4 });
  assert.equal(state.awaitingAudio, true);
  assert.equal(globalThis.__lastAudio, undefined, 'must not play before the frame arrives');

  await handleAudioFrame(new Uint8Array([1, 2, 3, 4]).buffer);

  assert.equal(state.awaitingAudio, false);
  assert.ok(globalThis.__lastAudio, 'audio element created');
  assert.match(globalThis.__lastAudio.src, /^blob:/);
  assert.equal(state.currentAudio, globalThis.__lastAudio);
});

test('reply audio is dropped if the user is already recording again', async () => {
  installDom();
  resetUiRefs();
  resetState();
  state.awaitingAudio = true;
  state.isRecording = true;

  await handleAudioFrame(new Uint8Array([1]).buffer);

  assert.equal(globalThis.__lastAudio, undefined);
  assert.equal(state.awaitingAudio, false);
});

test('unexpected binary frames are ignored', async () => {
  installDom();
  resetUiRefs();
  resetState();

  await handleAudioFrame(new Uint8Array([1]).buffer);

  assert.equal(globalThis.__lastAudio, undefined);
});

test('tool events render and update in place', async () => {
  installDom();
  resetUiRefs();
  resetState();

  await handleServerMessage({ type: 'agent.tool_start', id: 't1', name: 'web.search', summary: 'Searching' });
  assert.equal(lastMessage().innerText, '⚙ Searching…');

  await handleServerMessage({ type: 'agent.tool_end', id: 't1', ok: true, summary: 'Found 3 results' });
  assert.equal(document.getElementById('display').children.length, 1);
  assert.equal(lastMessage().innerText, '✓ Found 3 results');
});

test('error messages clear pending audio and show the message', async () => {
  installDom();
  resetUiRefs();
  resetState();
  state.awaitingAudio = true;

  await handleServerMessage({ type: 'error', code: 'tts_unavailable', message: 'Speech service is offline' });

  assert.equal(state.awaitingAudio, false);
  assert.equal(document.getElementById('status').innerText, 'Error: Speech service is offline');
});

test('session id is remembered for reconnects', async () => {
  installDom();
  resetUiRefs();
  resetState();

  await handleServerMessage({ type: 'session', session_id: 'abc123', protocol: 1, agent: 'echo' });

  assert.equal(state.sessionId, 'abc123');
});
