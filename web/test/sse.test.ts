import { describe, expect, it } from "vitest";
import { SseParser } from "../src/api/sse";
import { enc, parseChunks, rng, SCENARIOS, splitAt, streamBytes } from "./helpers";

describe("SseParser", () => {
  it("parses a whole recorded stream", () => {
    const evs = parseChunks([streamBytes("answer")]);
    expect(evs.map((e) => e.event)).toEqual([
      "route",
      "sources",
      "notice",
      "sentence",
      "sentence",
      "sentence",
      "receipt",
      "done",
    ]);
    for (const e of evs) expect(() => JSON.parse(e.data)).not.toThrow();
  });

  it.each(SCENARIOS)("gives the same events for every single cut point in %s", (name) => {
    const bytes = streamBytes(name);
    const whole = parseChunks([bytes]);
    for (let cut = 0; cut <= bytes.length; cut++) {
      expect(parseChunks(splitAt(bytes, [cut]))).toEqual(whole);
    }
  });

  it("gives the same events when fed one byte at a time", () => {
    const bytes = streamBytes("answer");
    const chunks = Array.from(bytes, (_, i) => bytes.subarray(i, i + 1));
    expect(parseChunks(chunks)).toEqual(parseChunks([bytes]));
  });

  it("gives the same events for random multi-cut splits", () => {
    const next = rng(124);
    for (const name of SCENARIOS) {
      const bytes = streamBytes(name);
      const whole = parseChunks([bytes]);
      for (let n = 0; n < 200; n++) {
        const cuts = Array.from({ length: 1 + Math.floor(next() * 12) }, () => Math.floor(next() * bytes.length));
        expect(parseChunks(splitAt(bytes, cuts))).toEqual(whole);
      }
    }
  });

  it("decodes a UTF-8 sequence cut in the middle", () => {
    const bytes = enc('event: notice\ndata: {"m":"£123,543 and a week’s pay"}\n\n');
    const pound = bytes.indexOf(0xc2);
    const quote = bytes.indexOf(0xe2);
    for (const cuts of [[pound + 1], [quote + 1], [quote + 2], [pound + 1, quote + 1, quote + 2]]) {
      const [ev] = parseChunks(splitAt(bytes, cuts));
      expect(ev?.data).toBe('{"m":"£123,543 and a week’s pay"}');
    }
  });

  it("accepts CRLF and lone CR line endings, including a CRLF split across chunks", () => {
    const lf = "event: route\ndata: {}\n\nevent: done\ndata: {}\n\n";
    const crlf = lf.replace(/\n/g, "\r\n");
    const cr = lf.replace(/\n/g, "\r");
    const want = [
      { event: "route", data: "{}" },
      { event: "done", data: "{}" },
    ];
    expect(parseChunks([enc(crlf)])).toEqual(want);
    expect(parseChunks([enc(cr)])).toEqual(want);
    const b = enc(crlf);
    for (let cut = 0; cut <= b.length; cut++) expect(parseChunks(splitAt(b, [cut]))).toEqual(want);
    const c = enc(cr);
    for (let cut = 0; cut <= c.length; cut++) expect(parseChunks(splitAt(c, [cut]))).toEqual(want);
  });

  it("does not take a CR at the end of a chunk as a blank line when LF follows", () => {
    const p = new SseParser();
    expect(p.push(enc("event: a\r\ndata: 1\r"))).toEqual([]);
    expect(p.push(enc("\n\r\n"))).toEqual([{ event: "a", data: "1" }]);
  });

  it("skips comments, unknown fields, id and retry", () => {
    const s = ": keep-alive\n\nretry: 10\nid: 7\nfoo: bar\n:comment\nevent: notice\ndata: {}\n\n";
    expect(parseChunks([enc(s)])).toEqual([{ event: "notice", data: "{}" }]);
  });

  it("joins several data lines, strips one leading space, and handles a bare field", () => {
    const s = "event: x\ndata:a\ndata:  b\ndata\n\n";
    expect(parseChunks([enc(s)])).toEqual([{ event: "x", data: "a\n b\n" }]);
  });

  it("drops a leading BOM", () => {
    const bytes = new Uint8Array([0xef, 0xbb, 0xbf, ...enc("event: done\ndata: {}\n\n")]);
    expect(parseChunks(splitAt(bytes, [1, 2]))).toEqual([{ event: "done", data: "{}" }]);
  });

  it("names an event without an event field 'message' and ignores blocks with no data", () => {
    expect(parseChunks([enc("event: lonely\n\ndata: 1\n\n")])).toEqual([{ event: "message", data: "1" }]);
  });

  it("discards an event cut off by the end of the stream", () => {
    expect(parseChunks([enc("event: route\ndata: {}\n\nevent: done\ndata: {}")])).toEqual([
      { event: "route", data: "{}" },
    ]);
  });
});
