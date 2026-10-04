import "vitest";
import "@testing-library/jest-dom";

declare module "vitest" {
  interface Assertion<T = any> {
    toHaveNoViolations(): T;
  }
}
