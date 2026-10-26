/** @type {import('tailwindcss').Config} */
// Syena (Sanskrit: falcon): a night-desk palette. Marigold is the brand and the only action colour;
// emerald / coral carry gain / loss, sky carries information. Token names are kept so every page restyles.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        dusk: { DEFAULT: "#0E1422", panel: "#151D2E", raised: "#1D2740", line: "#283350", deep: "#0A0F1A" },
        parch: { DEFAULT: "#EEE8DB", dim: "#B7B1A4" },
        mute: "#858DA3",
        saffron: { DEFAULT: "#F2A93B", deep: "#D68D1F", soft: "#3A2D17" },
        gain: { DEFAULT: "#3FC48A", soft: "#163A2C" },
        loss: { DEFAULT: "#F2685A", soft: "#45201C" },
        sky: { DEFAULT: "#7DB4F5", soft: "#1A2C47" },
      },
      fontFamily: {
        display: ['"Bricolage Grotesque Variable"', "system-ui", "sans-serif"],
        sans: ['"Figtree Variable"', "system-ui", "sans-serif"],
      },
      fontSize: { "2xs": ["12px", "16px"] },
      borderRadius: { panel: "16px" },
      boxShadow: {
        hero: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 24px 48px -24px rgba(0,0,0,0.6)",
        pop: "0 12px 32px -8px rgba(0,0,0,0.55)",
      },
    },
  },
  plugins: [],
};
