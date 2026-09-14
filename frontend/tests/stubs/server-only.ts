/**
 * Stand-in for the `server-only` package under Vitest.
 *
 * The real module throws unless it is resolved through React's `react-server` condition,
 * which Next.js sets and a test runner does not. Aliasing it here lets the server modules
 * be unit-tested while the import keeps doing its real job — failing the Next build if a
 * client component ever imports one of them.
 */
export {};
