// R-070: ESLint 9 flat config for the root TanStack Start app (src/).
// `npm run lint` existed with all plugins installed, but no config file, so it
// always exited 2. Only already-declared devDependencies are used.
// web/ is a separate Vite project (own package.json) and is not linted here.
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import eslintConfigPrettier from "eslint-config-prettier";

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      ".output/**",
      ".vinxi/**",
      ".tanstack/**",
      "node_modules/**",
      "web/**",
      "docs/**",
      "agent_civilization/**",
      "agos_kernel/**",
      "public/**",
      "src/routeTree.gen.ts",
    ],
  },
  {
    files: ["src/**/*.{ts,tsx}", "vite.config.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
  // Formatting is Prettier's job: disable stylistic rules that conflict with it.
  eslintConfigPrettier,
);
