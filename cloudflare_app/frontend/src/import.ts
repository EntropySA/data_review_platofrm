export type QuestionRecord = { id: number; instruction: string; input: string[]; output: string };
export type ValidationResult = { valid: QuestionRecord[]; errors: { row: number; message: string }[] };

export function validateDocument(document: unknown): ValidationResult {
  if (!document || typeof document !== "object" || !Array.isArray((document as { data?: unknown }).data)) {
    throw new Error("The JSON root must contain a data array.");
  }
  const valid: QuestionRecord[] = [];
  const errors: ValidationResult["errors"] = [];
  (document as { data: unknown[] }).data.forEach((value, index) => {
    const row = index + 1;
    if (!value || typeof value !== "object") return errors.push({ row, message: "Record must be an object." });
    const record = value as Record<string, unknown>;
    if (!Number.isInteger(record.id)) return errors.push({ row, message: "id must be an integer." });
    if (typeof record.instruction !== "string") return errors.push({ row, message: "instruction must be a string." });
    if (!Array.isArray(record.input) || !record.input.every((item) => typeof item === "string"))
      return errors.push({ row, message: "input must be an array of strings." });
    if (typeof record.output !== "string") return errors.push({ row, message: "output must be a string." });
    valid.push({ id: record.id as number, instruction: record.instruction, input: record.input as string[], output: record.output });
  });
  return { valid, errors };
}
