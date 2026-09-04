import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // `_`-prefixed params are deliberately unused — kept for signature shape.
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      // React Compiler rule; an error by default since eslint-plugin-react-hooks
      // v6. It fires on our fetch-on-mount effects (`setLoading(true)` ahead of
      // an async call), which are legitimate. A warning keeps it visible without
      // gating CI. The prop-to-state syncs it also flags — PlanLibrary saveName,
      // CalculatorWizard loadedPlan — are worth restructuring properly.
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
])
