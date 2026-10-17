/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        dusk: { DEFAULT: "#141923", panel: "#1B2130", raised: "#262E40", line: "#2F384C" },
        parch: { DEFAULT: "#ECE6D8", dim: "#B9B3A6" },
        mute: "#8B93A7",
        saffron: { DEFAULT: "#E8A33D", deep: "#C7841F", soft: "#3A2F1E" },
        gain: { DEFAULT: "#56B488", soft: "#1D3A30" },
        loss: { DEFAULT: "#E36B5D", soft: "#43231F" },
        sky: "#7FA8D8",
      },
      fontFamily: {
        display: ['"Bricolage Grotesque Variable"', "system-ui", "sans-serif"],
        sans: ['"Figtree Variable"', "system-ui", "sans-serif"],
      },
      borderRadius: { panel: "14px" },
    },
  },
  plugins: [],
};
