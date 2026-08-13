import { describe, expect, it } from "vitest";
import { validateDocument } from "./import";

describe("JSON validation", () => {
  it("keeps valid template fields and reports invalid rows", () => {
    const result = validateDocument({ data: [
      { id: 1, instruction: "أجب", input: ["سؤال"], output: "جواب", ignored: true },
      { id: 2, instruction: "bad", input: [3], output: "x" },
    ] });
    expect(result.valid).toEqual([{ id: 1, instruction: "أجب", input: ["سؤال"], output: "جواب" }]);
    expect(result.errors).toHaveLength(1);
  });
});
