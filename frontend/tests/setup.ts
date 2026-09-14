import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Component tests share one jsdom per file; without this, a query in the second test
// matches elements the first one left behind and the failure looks like a duplicate render.
afterEach(() => {
  cleanup();
});
