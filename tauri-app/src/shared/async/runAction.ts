import type { DomainError } from "@/shared/types";

export interface RunActionOptions<T> {
  action: () => Promise<T>;
  before?: () => void;
  success?: (result: T) => void;
  failure?: (error: DomainError) => void;
  after?: () => void;
  mapError?: (error: unknown) => DomainError;
}

function toDomainError(error: unknown): DomainError {
  if (error instanceof Error) {
    return { code: "runtime_error", message: error.message, cause: error };
  }
  return { code: "runtime_error", message: String(error), cause: error };
}

export async function runAction<T>(options: RunActionOptions<T>): Promise<T> {
  const { action, before, success, failure, after, mapError } = options;
  before?.();
  try {
    const result = await action();
    success?.(result);
    return result;
  } catch (error) {
    const mapped = mapError ? mapError(error) : toDomainError(error);
    failure?.(mapped);
    throw mapped;
  } finally {
    after?.();
  }
}
