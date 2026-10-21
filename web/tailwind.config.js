/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // The display face: a condensed grotesk, the way a broadcast puts a
        // score or a headline on screen. Self-hosted, so it is the same
        // everywhere and costs no third-party request.
        display: [
          "Barlow Condensed",
          "Avenir Next Condensed",
          "Roboto Condensed",
          "Arial Narrow",
          "Helvetica Neue",
          "sans-serif",
        ],
        // The body face: a clean grotesque for copy, controls and figures.
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      fontSize: {
        // A real type scale, not one size at four weights. Each step has
        // its own voice: display, headline, title, body, small, and the
        // micro-labels that carry the routine metadata.
        display: ["2.5rem", { lineHeight: "1", letterSpacing: "0.005em" }],
        score: ["1.75rem", { lineHeight: "1", letterSpacing: "0.01em" }],
        headline: ["1.125rem", { lineHeight: "1.25", letterSpacing: "0.005em" }],
        title: ["0.9375rem", { lineHeight: "1.3", letterSpacing: "0.005em" }],
        body: ["0.8125rem", { lineHeight: "1.55" }],
        small: ["0.75rem", { lineHeight: "1.45" }],
        micro: ["0.6875rem", { lineHeight: "1.25", letterSpacing: "0.06em" }],
        nano: ["0.625rem", { lineHeight: "1.2", letterSpacing: "0.08em" }],
      },
      colors: {
        // The neutral ramp. A single family for every surface, every
        // border and every piece of copy - so the accent is the only
        // colour on the page that means anything.
        //
        // Low numbers are light (text), high numbers are dark (surfaces),
        // the way a colour scale is normally read.
        ink: {
          50: "#f2f6f3",
          100: "#dfe7e2",
          200: "#bcc9c1",
          300: "#adbbb3",
          400: "#95a69c",
          500: "#81948a",
          600: "#647b6e",
          700: "#2a3630",
          800: "#1c2620",
          850: "#16221c",
          900: "#121a16",
          950: "#0a0f0c",
        },
        // The one accent: the pitch green. Live provider, confidence, the
        // active tab, the ball on the pitch. Nothing else is allowed to
        // be a colour.
        pitch: {
          50: "#e9f9ef",
          100: "#c6f0d6",
          200: "#8fe0af",
          300: "#57cb87",
          400: "#33b26a",
          500: "#1f9455",
          600: "#167545",
          700: "#135c39",
          800: "#12482f",
          900: "#0f3a27",
          950: "#061f14",
        },
        // Status, and only status: something degraded, something refused.
        // Never a series colour, never decoration.
        signal: {
          amber: "#e3a94a",
          red: "#ef5f5f",
        },
      },
      boxShadow: {
        // One shadow, for the one surface that earns it.
        card: "0 1px 0 0 rgba(255,255,255,0.03), 0 8px 24px -12px rgba(0,0,0,0.7)",
      },
      keyframes: {
        "card-in": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
      },
      animation: {
        "card-in": "card-in 320ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "pulse-soft": "pulse-soft 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
