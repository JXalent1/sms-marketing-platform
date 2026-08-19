/** @type {import('tailwindcss').Config} */

// Colors are CSS custom properties, not literal hex values, for two reasons:
//
//   1. Theming. `bg-surface` resolves differently under `[data-theme="light"]`
//      without a second set of utility classes or a `dark:` prefix on every
//      element.
//   2. The brand hex arrives from .env at request time. A build-time literal
//      could not be overridden without recompiling the stylesheet on the server.
//
// Every token here is defined in app/assets/tailwind.css. Adding a utility name
// without adding the variable produces an invalid declaration that fails
// silently, so keep the two lists in step.
const token = (name) => `var(--${name})`;

module.exports = {
  content: ["./app/templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        page: token("page"),
        surface: token("surface"),
        "surface-2": token("surface-2"),

        ink: token("ink"),
        "ink-2": token("ink-2"),
        "ink-3": token("ink-3"),

        line: token("line"),
        "line-strong": token("line-strong"),

        brand: token("brand"),
        "brand-soft": token("brand-soft"),
        "on-brand": token("on-brand"),

        accent: token("accent"),
        "accent-soft": token("accent-soft"),

        // Category hues. Validated as a set — see the note in tailwind.css.
        s1: token("s1"),
        s2: token("s2"),
        s3: token("s3"),
        s4: token("s4"),

        good: token("good"),
        "good-ink": token("good-ink"),
        "good-soft": token("good-soft"),

        "warn-ink": token("warn-ink"),
        "warn-soft": token("warn-soft"),

        crit: token("crit"),
        "crit-soft": token("crit-soft"),
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
    },
  },
  plugins: [],
};
