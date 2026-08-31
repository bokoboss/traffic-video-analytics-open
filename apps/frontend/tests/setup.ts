import "@testing-library/jest-dom/vitest";

const localStorageValues = new Map<string, string>();

Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: {
    clear: () => localStorageValues.clear(),
    getItem: (key: string) => localStorageValues.get(key) ?? null,
    key: (index: number) => Array.from(localStorageValues.keys())[index] ?? null,
    get length() {
      return localStorageValues.size;
    },
    removeItem: (key: string) => {
      localStorageValues.delete(key);
    },
    setItem: (key: string, value: string) => {
      localStorageValues.set(key, value);
    }
  }
});
