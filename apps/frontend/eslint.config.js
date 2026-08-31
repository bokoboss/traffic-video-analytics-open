import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["dist/**", "coverage/**", "test-results/**", "playwright-report/**"]
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}", "tests/**/*.{ts,tsx}"],
    languageOptions: {
      globals: {
        document: "readonly",
        window: "readonly",
        React: "readonly"
      }
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error"
    }
  }
);
