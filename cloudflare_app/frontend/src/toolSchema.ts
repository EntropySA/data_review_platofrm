// Instructions sometimes carry a function-calling tool schema: an array of
// function definitions, each with parameters and a required list. Printed as
// plain JSON these run to hundreds of lines, so they are recognised here and
// reduced to the fields a reviewer actually checks.
export type ToolParam = { name: string; type: string; description: string; required: boolean };

export type ToolFunction = {
  name: string;
  description: string;
  params: ToolParam[];
  required: string[];
  /** Names in `required` with no matching property. A defect worth showing. */
  missingRequired: string[];
};

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

/** The definition inside one array entry, whether it is wrapped in the
 *  {type:"function", function:{...}} form or given bare. */
function definition(item: unknown): Record<string, unknown> | undefined {
  const entry = record(item);
  if (!entry) return undefined;
  const wrapped = record(entry.function);
  if (wrapped && typeof wrapped.name === "string") return wrapped;
  if (typeof entry.name === "string" && record(entry.parameters)) return entry;
  return undefined;
}

function normalise(fn: Record<string, unknown>): ToolFunction {
  const parameters = record(fn.parameters) ?? {};
  const properties = record(parameters.properties) ?? {};
  const required = Array.isArray(parameters.required)
    ? parameters.required.filter((name): name is string => typeof name === "string")
    : [];
  const params = Object.entries(properties).map(([name, spec]) => {
    const detail = record(spec) ?? {};
    return {
      name,
      type: typeof detail.type === "string" ? detail.type : "",
      description: typeof detail.description === "string" ? detail.description : "",
      required: required.includes(name),
    };
  });
  return {
    name: typeof fn.name === "string" ? fn.name : "",
    description: typeof fn.description === "string" ? fn.description : "",
    params,
    required,
    missingRequired: required.filter((name) => !(name in properties)),
  };
}

/** The functions described, or undefined when this is not a tool schema.
 *  Every entry must match, so an ordinary array of objects is left alone. */
export function asToolSchema(value: unknown): ToolFunction[] | undefined {
  if (!Array.isArray(value) || value.length === 0) return undefined;
  const definitions = value.map(definition);
  if (definitions.some((fn) => fn === undefined)) return undefined;
  return (definitions as Record<string, unknown>[]).map(normalise);
}
