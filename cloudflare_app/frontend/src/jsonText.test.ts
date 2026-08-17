import { describe, expect, it } from "vitest";
import { containsJson, extractJson } from "./jsonText";

describe("JSON detection in reviewed text", () => {
  it("treats a field that is entirely JSON as one block", () => {
    const segments = extractJson('{"answer": "الرياض", "score": 3}');
    expect(segments).toHaveLength(1);
    expect(segments[0].json).toEqual({ answer: "الرياض", score: 3 });
  });

  it("keeps prose around an embedded object", () => {
    const segments = extractJson('اقرأ التالي: {"a": 1} ثم أجب');
    expect(segments.map((s) => s.json !== undefined)).toEqual([false, true, false]);
    expect(segments[0].text).toBe("اقرأ التالي: ");
    expect(segments[1].json).toEqual({ a: 1 });
    expect(segments[2].text).toBe(" ثم أجب");
  });

  it("leaves a template placeholder alone", () => {
    const text = "من النص التالي{input}، استخرج الكلمات";
    const segments = extractJson(text);
    expect(containsJson(segments)).toBe(false);
    expect(segments.map((s) => s.text).join("")).toBe(text);
  });

  it("unwraps a fenced json block", () => {
    const segments = extractJson('Result:\n```json\n{"ok": true}\n```\ndone');
    expect(segments[1].json).toEqual({ ok: true });
    expect(segments[2].text).toBe("\ndone");
  });

  it("is not fooled by braces inside strings", () => {
    const segments = extractJson('{"tpl": "a { b } c", "n": [1, 2]}');
    expect(segments).toHaveLength(1);
    expect(segments[0].json).toEqual({ tpl: "a { b } c", n: [1, 2] });
  });

  it("handles a top-level array", () => {
    const segments = extractJson('[{"id": 1}, {"id": 2}]');
    expect(segments[0].json).toEqual([{ id: 1 }, { id: 2 }]);
  });

  it("does not reformat a bare number or quoted word", () => {
    expect(containsJson(extractJson("42"))).toBe(false);
    expect(containsJson(extractJson('"just a string"'))).toBe(false);
  });

  it("leaves malformed JSON as prose so the reviewer sees it verbatim", () => {
    const text = '{"unclosed": 1';
    expect(containsJson(extractJson(text))).toBe(false);
    expect(extractJson(text)[0].text).toBe(text);
  });

  it("preserves the original text across every segment", () => {
    const text = 'before {"a": {"b": [1]}} middle {not json} after';
    expect(extractJson(text).map((s) => s.text).join("")).toBe(text);
  });
});
