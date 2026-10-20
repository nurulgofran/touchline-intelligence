/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
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
      colors: {
        // A single dark, slightly blue-grey ramp for the whole studio.
        // Low numbers are light (text), high numbers are dark (surfaces).
        studio: {
          50: "#f8fafc",
          100: "#eef2f7",
          200: "#dbe3ec",
          300: "#b3c0cf",
          400: "#8494a8",
          500: "#5c6c82",
          600: "#44546b",
          700: "#334155",
          800: "#232f42",
          850: "#1a2434",
          900: "#121a27",
          950: "#0a0f18",
        },
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
