import '@testing-library/jest-dom/vitest';

// Mock window.location.hash for navigation
Object.defineProperty(window, 'location', {
  value: {
    ...window.location,
    hash: '#/',
    origin: 'http://localhost:3000',
    href: 'http://localhost:3000/#/',
    pathname: '/',
    search: '',
    assign: vi.fn(),
    replace: vi.fn(),
    reload: vi.fn(),
  },
  writable: true,
});

// Mock localStorage
const store = {};
Object.defineProperty(window, 'localStorage', {
  value: {
    getItem: vi.fn((key) => store[key] ?? null),
    setItem: vi.fn((key, value) => {
      store[key] = String(value);
    }),
    removeItem: vi.fn((key) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      Object.keys(store).forEach((key) => delete store[key]);
    }),
  },
});

// Mock navigator.serviceWorker
Object.defineProperty(navigator, 'serviceWorker', {
  value: {
    controller: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    ready: Promise.resolve({ sync: { register: vi.fn() } }),
  },
  configurable: true,
});

// Mock matchMedia
window.matchMedia = vi.fn().mockReturnValue({
  matches: false,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
});

// Mock confirm
window.confirm = vi.fn().mockReturnValue(true);

// Mock scrollIntoView
Element.prototype.scrollIntoView = vi.fn();
