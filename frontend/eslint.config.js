import js from '@eslint/js';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import prettier from 'eslint-config-prettier';
import globals from 'globals';

export default [
  js.configs.recommended,

  // JSX accessibility
  jsxA11y.flatConfigs.recommended,

  // Prettier — disables conflicting rules (must be last override)
  prettier,

  {
    files: ['src/**/*.{js,jsx}', 'tests/**/*.{js,jsx}'],
    plugins: {
      react,
      'react-hooks': reactHooks,
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    settings: {
      react: {
        pragma: 'h',
        version: '17', // Preact uses the same JSX transform API
      },
    },
    rules: {
      // React / Preact
      'react/jsx-uses-react': 'error',
      'react/jsx-uses-vars': 'error',
      'react/no-unknown-property': [
        'error',
        {
          ignore: [
            'class',
            'for',
            // Preact uses HTML attribute names, not React camelCase
            'autocomplete',
            'autofocus',
            'stroke-width',
            'stroke-linecap',
            'stroke-linejoin',
            'fill-rule',
            'clip-rule',
            'text-anchor',
            'dominant-baseline',
            'font-family',
            'font-weight',
            'font-size',
          ],
        },
      ],
      'react/prop-types': 'off',

      // Hooks
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      // Relax some defaults for this codebase
      'no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
    },
  },

  // Test files can use vitest globals
  {
    files: ['tests/**/*.{js,jsx}'],
    languageOptions: {
      globals: {
        ...globals.node,
        describe: 'readonly',
        it: 'readonly',
        expect: 'readonly',
        vi: 'readonly',
        beforeEach: 'readonly',
        afterEach: 'readonly',
        beforeAll: 'readonly',
        afterAll: 'readonly',
        test: 'readonly',
      },
    },
  },

  {
    ignores: ['node_modules/', 'dist/', '../assets/'],
  },
];
