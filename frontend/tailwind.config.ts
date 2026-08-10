import type { Config } from "tailwindcss";
const config: Config = { content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"], theme: { extend: { colors: { ink: "#15231f", moss: "#254d3f", mint: "#dcebe3", stone: "#f5f4f0", line: "#dce1dc", coral: "#d96d50" }, boxShadow: { soft: "0 18px 55px rgba(23, 45, 37, .09)" } } }, plugins: [] };
export default config;
