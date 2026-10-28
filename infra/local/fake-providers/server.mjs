// Fake Gemini and Azure Speech endpoints for the local stack. They speak the
// same request/response shapes the agent's clients use, but return placeholder
// Vietnamese text and a sine tone instead of real translation and speech.
import { createServer } from "node:https";
import { readFileSync } from "node:fs";

const env = process.env;
const ttsCharactersPerSecond = 15;

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => resolve(Buffer.concat(chunks)));
    request.on("error", reject);
  });
}

function json(response, status, value) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(value));
}

// Placeholder line sized to ~80% of the source window at the fake TTS rate,
// so the default local run fits the timing rules.
function fakeVietnamese(segment, index) {
  const targetCharacters = Math.max(8, Math.round(((segment.endMs - segment.startMs) / 1000) * ttsCharactersPerSecond * 0.8));
  let text = `Câu ${index + 1}:`;
  while (text.length < targetCharacters) text += " thử nghiệm";
  return `${text.slice(0, targetCharacters).trimEnd()}.`;
}

function translate(body) {
  const prompt = JSON.parse(body.toString("utf8")).contents[0].parts[0].text;
  const line = prompt.split("\n").find((value) => value.startsWith("UNTRUSTED_SOURCE_SEGMENTS_JSON="));
  const segments = JSON.parse(line.slice("UNTRUSTED_SOURCE_SEGMENTS_JSON=".length));
  const translated = segments.map((segment, index) => ({
    endMs: segment.endMs,
    segmentId: segment.segmentId,
    startMs: segment.startMs,
    text: fakeVietnamese(segment, index),
  }));
  return { candidates: [{ content: { parts: [{ text: JSON.stringify(translated) }] } }] };
}

function wav(seconds) {
  const sampleRate = 16000;
  const samples = Math.round(seconds * sampleRate);
  const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + samples * 2, 4);
  buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(samples * 2, 40);
  for (let index = 0; index < samples; index += 1) {
    const fade = Math.min(1, index / 800, (samples - index) / 800);
    buffer.writeInt16LE(Math.round(Math.sin((2 * Math.PI * 220 * index) / sampleRate) * 6000 * fade), 44 + index * 2);
  }
  return buffer;
}

function synthesize(body) {
  const ssml = body.toString("utf8");
  const text = ssml.replace(/<[^>]+>/g, "").trim();
  return wav(Math.min(12, Math.max(0.3, text.length / ttsCharactersPerSecond)));
}

const server = createServer(
  { cert: readFileSync("/certs/server.pem"), key: readFileSync("/certs/server-key.pem") },
  async (request, response) => {
    try {
      const body = await readBody(request);
      if (request.method === "POST" && /^\/v1beta\/models\/[^/]+:generateContent$/.test(request.url)) {
        if (request.headers["x-goog-api-key"] !== env.FAKE_GEMINI_KEY) return json(response, 401, { error: "bad key" });
        return json(response, 200, translate(body));
      }
      if (request.method === "POST" && request.url === "/cognitiveservices/v1") {
        if (request.headers["ocp-apim-subscription-key"] !== env.FAKE_AZURE_KEY) return json(response, 401, { error: "bad key" });
        if (request.headers["x-microsoft-outputformat"] !== "riff-16khz-16bit-mono-pcm") return json(response, 400, { error: "format" });
        response.writeHead(200, { "content-type": "audio/wav" });
        return response.end(synthesize(body));
      }
      json(response, 404, { error: "not found" });
    } catch (error) {
      console.error("fake provider error", error);
      json(response, 400, { error: "bad request" });
    }
  },
);

server.listen(443, () => console.log("fake providers listening on :443"));
