import { describe, expect, it } from "vitest";
import { asToolSchema } from "./toolSchema";

const wrapped = [{
  type: "function",
  function: {
    name: "get_weather",
    description: "يعيد حالة الطقس",
    parameters: {
      type: "object",
      properties: {
        city: { type: "string", description: "اسم المدينة" },
        unit: { type: "string", description: "c or f" },
      },
      required: ["city"],
    },
  },
}];

describe("tool schema detection", () => {
  it("reads the wrapped function form", () => {
    const [fn] = asToolSchema(wrapped)!;
    expect(fn.name).toBe("get_weather");
    expect(fn.description).toBe("يعيد حالة الطقس");
    expect(fn.params.map((p) => [p.name, p.type, p.required]))
      .toEqual([["city", "string", true], ["unit", "string", false]]);
  });

  it("reads a bare definition without the type wrapper", () => {
    const [fn] = asToolSchema([{ name: "ping", parameters: { properties: { host: { type: "string" } } } }])!;
    expect(fn.name).toBe("ping");
    expect(fn.params).toHaveLength(1);
  });

  it("flags a required name that is not a declared property", () => {
    const [fn] = asToolSchema([{
      function: { name: "f", parameters: { properties: { a: { type: "string" } }, required: ["a", "ghost"] } },
    }])!;
    expect(fn.missingRequired).toEqual(["ghost"]);
  });

  it("ignores an ordinary array of objects", () => {
    expect(asToolSchema([{ id: 1 }, { id: 2 }])).toBeUndefined();
  });

  it("ignores an array where only some entries are functions", () => {
    expect(asToolSchema([wrapped[0], { id: 2 }])).toBeUndefined();
  });

  it("ignores objects, empty arrays and primitives", () => {
    expect(asToolSchema({ function: { name: "f" } })).toBeUndefined();
    expect(asToolSchema([])).toBeUndefined();
    expect(asToolSchema("[]")).toBeUndefined();
  });

  it("survives a definition missing description, parameters or properties", () => {
    const [fn] = asToolSchema([{ function: { name: "bare" } }])!;
    expect(fn.description).toBe("");
    expect(fn.params).toEqual([]);
    expect(fn.required).toEqual([]);
  });

  it("keeps the template's empty placeholder names rather than dropping them", () => {
    const [fn] = asToolSchema([{
      type: "function",
      function: { name: "", description: "", parameters: { type: "", properties: { "": { type: "", description: "" } }, required: [""] } },
    }])!;
    expect(fn.name).toBe("");
    expect(fn.params).toHaveLength(1);
    expect(fn.params[0].required).toBe(true);
  });
});
